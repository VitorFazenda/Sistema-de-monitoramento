import datetime
import gc
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dotenv import load_dotenv
import pandas as pd
from playwright.sync_api import sync_playwright
import requests
from sqlalchemy import create_engine, inspect, text

# ==========================================
# CAMINHOS ABSOLUTOS (PREPARADO PARA SERVIDOR LINUX E LOCAL)
# ==========================================
DIRETORIO_ROBO = os.path.dirname(os.path.abspath(__file__))
DIRETORIO_RAIZ = os.path.abspath(os.path.join(DIRETORIO_ROBO, ".."))
CAMINHO_ENV = os.path.join(DIRETORIO_RAIZ, "backend", ".env")
PASTA_SESSAO = os.path.join(DIRETORIO_ROBO, "sessao_meli")
PASTA_TEMP = os.path.join(DIRETORIO_RAIZ, "temp")
ARQUIVO_ULTIMA_CONTA = os.path.join(DIRETORIO_ROBO, "ultima_conta_meli.json")

load_dotenv(CAMINHO_ENV)

DB_URL = os.getenv("DATABASE_URL")
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
MELI_USER = os.getenv("MELI_USER", "ext_vifazend")
MELI_PASS = os.getenv("MELI_PASS", "Palido%2696%")
OKTA_SECRET = os.getenv("OKTA_SECRET", "").strip()
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "False").lower() in (
    "true",
    "1",
    "t",
)

# ==========================================
# MAPEAMENTO SEGURO DE CONTAS
# ==========================================
CONTAS_MELI = {
    "Vitor.Martins": {"user": MELI_USER, "pass": MELI_PASS},
    "Larissa.Campos": {
        "user": os.getenv("MELI_USER_2", "ext_camplari"),
        "pass": os.getenv("MELI_PASS_2", "Ensalada@0533."),
    },
}


def carregar_ultima_conta_logada() -> str:
  """Recupera a última conta que foi utilizada para logar"""
  try:
    if os.path.exists(ARQUIVO_ULTIMA_CONTA):
      with open(ARQUIVO_ULTIMA_CONTA, "r", encoding="utf-8") as f:
        dados = json.load(f)
        conta = dados.get("conta", "Vitor.Martins")
        if conta in CONTAS_MELI:
          return conta
  except Exception:
    pass
  return "Vitor.Martins"


def salvar_ultima_conta_logada(nome_conta: str):
  """Salva a conta atual para que na próxima expiração de sessão tente logar nela"""
  try:
    with open(ARQUIVO_ULTIMA_CONTA, "w", encoding="utf-8") as f:
      json.dump({"conta": nome_conta}, f)
  except Exception:
    pass


CONTA_ATUAL = carregar_ultima_conta_logada()

if not DB_URL:
  print(
      "❌ Erro Crítico: DATABASE_URL não encontrada. Verifique o seu ficheiro"
      " .env!"
  )
  exit()

motor_db = create_engine(
    DB_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"options": "-c lock_timeout=15000 -c statement_timeout=60000"},
)
DIAS_SEMANA = [
    "Segunda",
    "Terca",
    "Quarta",
    "Quinta",
    "Sexta",
    "Sabado",
    "Domingo",
]

# ==========================================
# WATCHDOG E LIMPEZA DE PROCESSOS (ANTI-TRAVAMENTO)
# ==========================================
ULTIMA_ATIVIDADE_MOTOR = time.time()
EM_ESPERA_LOGIN_OKTA = False


def pulsar_watchdog():
  global ULTIMA_ATIVIDADE_MOTOR
  ULTIMA_ATIVIDADE_MOTOR = time.time()


def limpar_travas_sessao():
  """Remove arquivos SingletonLock caso o Chromium tenha fechado abruptamente no servidor"""
  if not os.path.exists(PASTA_SESSAO):
    return
  for nome_lock in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
    caminho_lock = os.path.join(PASTA_SESSAO, nome_lock)
    try:
      if os.path.lexists(caminho_lock):
        os.remove(caminho_lock)
    except Exception:
      pass


def matar_chromium_zumbi():
  """Força o encerramento de processos órfãos do Chromium no Linux"""
  if os.name != "nt":
    try:
      subprocess.run(
          ["pkill", "-9", "-f", "chromium"],
          stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL,
          timeout=5,
      )
      subprocess.run(
          ["pkill", "-9", "-f", "chrome"],
          stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL,
          timeout=5,
      )
    except Exception:
      pass
  limpar_travas_sessao()


def log_painel(mensagem):
  pulsar_watchdog()
  print(mensagem, flush=True)
  try:
    requests.post(
        f"{API_URL}/enviar_log", json={"mensagem": mensagem}, timeout=3
    )
  except Exception:
    pass


def informar_status_login(status_str):
  global EM_ESPERA_LOGIN_OKTA
  pulsar_watchdog()
  EM_ESPERA_LOGIN_OKTA = status_str == "AGUARDANDO_OKTA"
  try:
    requests.post(
        f"{API_URL}/status_login_robo",
        json={"status": status_str, "conta": CONTA_ATUAL},
        timeout=2,
    )
  except Exception:
    pass


def ler_comando_okta_api():
  """Consulta na API se o utilizador enviou código Okta, trocou a conta ou pediu para recarregar"""
  pulsar_watchdog()
  try:
    r = requests.get(f"{API_URL}/obter_comando_okta", timeout=3)
    if r.status_code == 200:
      return r.json()
  except Exception:
    pass
  return {
      "codigo": "",
      "reiniciar_login": False,
      "trocar_conta": "",
      "conta_ativa": CONTA_ATUAL,
  }


def gerar_codigo_totp_automatico():
  """Gera o código de 6 dígitos automaticamente se OKTA_SECRET estiver configurado no .env"""
  if not OKTA_SECRET:
    return None
  try:
    import pyotp

    totp = pyotp.TOTP(OKTA_SECRET.replace(" ", ""))
    return totp.now()
  except Exception as e:
    log_painel(f"⚠️ Erro ao gerar código TOTP automático: {e}")
    return None


def avaliar_se_deve_rodar(ticket):
  agora = datetime.datetime.now()
  tipo = ticket.get("tipo", "")
  status = ticket.get("status", "")

  if status == "CONCLUIDO":
    return False
  if tipo == "avulso" and status == "ATIVO":
    return True

  if tipo == "rotina":
    regras_str = ticket.get("regras", "{}")
    try:
      regras = (
          json.loads(regras_str) if isinstance(regras_str, str) else regras_str
      )
    except Exception:
      return False

    dia_hoje = DIAS_SEMANA[agora.weekday()]
    if dia_hoje not in regras:
      return False

    hora_atual_str = agora.strftime("%H:%M")
    ultima_coleta_str = str(ticket.get("ultima_coleta", "Aguardando"))
    ultima_coleta_dt = None
    if ultima_coleta_str not in ["Aguardando", "None", ""]:
      try:
        ultima_coleta_dt = datetime.datetime.strptime(
            ultima_coleta_str, "%Y-%m-%d %H:%M:%S"
        )
      except Exception:
        pass

    for slot in regras[dia_hoje]:
      ini = slot.get("ini", "00:00")
      fim = slot.get("fim", "23:59")
      intervalo = int(slot.get("intervalo", 60))

      if ini <= hora_atual_str <= fim:
        if not ultima_coleta_dt:
          return True
        minutos_passados = (agora - ultima_coleta_dt).total_seconds() / 60.0
        if minutos_passados >= intervalo:
          return True

  return False


def daemon_heartbeat():
  """Envia pulso para a API e reinicia o robô sozinho caso o motor principal congele"""
  while True:
    tempo_sem_resposta = time.time() - ULTIMA_ATIVIDADE_MOTOR
    limite_travamento = 600 if EM_ESPERA_LOGIN_OKTA else 240

    if tempo_sem_resposta > limite_travamento:
      try:
        msg_reset = (
            "🔄 [AUTO-RESET] Robô sem resposta há"
            f" {int(tempo_sem_resposta)}s. Limpando navegador e reiniciando"
            " automaticamente..."
        )
        print(msg_reset, flush=True)
        requests.post(
            f"{API_URL}/enviar_log", json={"mensagem": msg_reset}, timeout=3
        )
      except Exception:
        pass
      matar_chromium_zumbi()
      os._exit(1)

    try:
      requests.get(f"{API_URL}/heartbeat", timeout=10)
    except Exception:
      pass
    time.sleep(4)


# ==========================================
# MÓDULO DE LOGIN AUTOMATIZADO (MULTI-CONTA + AUTO-RELOAD 5 MIN)
# ==========================================
def clicar_botao_avancar_okta(page):
  """Localiza e clica no botão de submissão do Okta (Próximo / Verificar / Confirmar)"""
  seletores = [
      'input[type="submit"][value="Verificar"]',
      'input[type="submit"][value="Próximo"]',
      'input[type="submit"]',
      'button[type="submit"]',
      'input[data-type="save"]',
  ]
  for sel in seletores:
    loc = page.locator(sel)
    if loc.count() > 0 and loc.first.is_visible():
      loc.first.click()
      return True
  return False


def clicar_voltar_tela_login_okta(page):
  """Clica no botão 'Voltar à tela de login' (<a data-se="cancel" class="js-cancel cancelb">)"""
  seletores_voltar = [
      'a[data-se="cancel"]',
      "a.js-cancel.cancelb",
      'a:has-text("Voltar à tela de login")',
      'a:has-text("Sign out")',
  ]
  for sel in seletores_voltar:
    try:
      btn = page.locator(sel).first
      if btn.count() > 0 and btn.is_visible():
        btn.click(timeout=5000)
        time.sleep(3)
        return True
    except Exception:
      pass
  return False


def pagina_esta_no_okta(page):
  url_atual = (page.url or "").lower()
  if "auth-meli.adminml.com" in url_atual or "okta.com" in url_atual:
    return True
  try:
    if (
        page.locator(
            'input[name="identifier"], #okta-sign-in, [data-se="o-form"]'
        ).count()
        > 0
    ):
      return True
  except Exception:
    pass
  return False


def garantir_login(page, forcar_recarregamento=False):
  global CONTA_ATUAL
  CONTA_ATUAL = carregar_ultima_conta_logada()
  log_painel("🔎 Verificando sessão do Mercado Livre...")
  url_atual = page.url or ""

  try:
    if forcar_recarregamento or "envios.adminml.com" not in url_atual:
      page.goto(
          "https://envios.adminml.com/logistics/first-mile/monitoring",
          wait_until="domcontentloaded",
          timeout=60000,
      )
      time.sleep(5)
  except Exception as e:
    log_painel(f"⚠️ Aviso ao carregar URL inicial: {str(e).splitlines()[0]}")
    time.sleep(3)

  if not pagina_esta_no_okta(page):
    informar_status_login("LOGADO")
    log_painel("✅ Sessão ativa. Não foi necessário realizar login.")
    return True

  log_painel(
      "🔐 Autenticação necessária. Iniciando fluxo inteligente de Login Okta..."
  )
  informar_status_login("AGUARDANDO_OKTA")

  aviso_okta_enviado = False
  ultimo_user_preenchido = 0
  ultima_senha_preenchida = 0
  inicio_espera_okta = time.time()

  while pagina_esta_no_okta(page):
    pulsar_watchdog()
    cmd = ler_comando_okta_api()

    nova_conta = str(cmd.get("trocar_conta", "")).strip()
    if nova_conta and nova_conta in CONTAS_MELI:
      CONTA_ATUAL = nova_conta
      salvar_ultima_conta_logada(CONTA_ATUAL)
      informar_status_login("AGUARDANDO_OKTA")
      log_painel(
          f"🔄 Alternando para o login de {CONTA_ATUAL}. Clicando em 'Voltar à"
          " tela de login'..."
      )

      clicou_voltar = clicar_voltar_tela_login_okta(page)
      if not clicou_voltar:
        try:
          page.goto(
              "https://envios.adminml.com/logistics/first-mile/monitoring",
              wait_until="domcontentloaded",
              timeout=60000,
          )
          time.sleep(4)
        except Exception:
          pass

      aviso_okta_enviado = False
      ultimo_user_preenchido = 0
      ultima_senha_preenchida = 0
      inicio_espera_okta = time.time()
      continue

    tempo_esperando_okta = time.time() - inicio_espera_okta
    passou_5_minutos = aviso_okta_enviado and tempo_esperando_okta >= 300

    if cmd.get("reiniciar_login") or passou_5_minutos:
      if passou_5_minutos:
        log_painel(
            "⏰ 5 minutos aguardando código Okta. Recarregando a página"
            " automaticamente..."
        )
      else:
        log_painel(
            "🔄 Reiniciando página de login do Meli a pedido do painel..."
        )

      aviso_okta_enviado = False
      ultimo_user_preenchido = 0
      ultima_senha_preenchida = 0
      inicio_espera_okta = time.time()
      try:
        clicar_voltar_tela_login_okta(page)
        page.goto(
            "https://envios.adminml.com/logistics/first-mile/monitoring",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        time.sleep(4)
      except Exception as e:
        log_painel(f"⚠️ Erro ao recarregar página: {e}")
      continue

    codigo_recebido = str(cmd.get("codigo", "")).strip()
    credenciais_atuais = CONTAS_MELI.get(
        CONTA_ATUAL, CONTAS_MELI["Vitor.Martins"]
    )
    usuario_meli_atual = credenciais_atuais["user"]
    senha_meli_atual = credenciais_atuais["pass"]

    try:
      campo_user = page.locator('input[name="identifier"]')
      if campo_user.count() > 0 and campo_user.first.is_visible():
        campos_cred_visiveis = page.locator(
            'input[type="password"]:visible,'
            ' input[name="credentials.passcode"]:visible,'
            ' input[name="credentials.totp"]:visible'
        ).count()
        if (
            campos_cred_visiveis == 0
            and (time.time() - ultimo_user_preenchido) > 8
        ):
          log_painel(
              f"👤 Preenchendo utilizador ({usuario_meli_atual}) e avançando..."
          )
          campo_user.first.fill("")
          campo_user.first.fill(usuario_meli_atual)
          clicar_botao_avancar_okta(page)
          ultimo_user_preenchido = time.time()
          aviso_okta_enviado = False
          inicio_espera_okta = time.time()
          time.sleep(3.5)
          continue

      btn_fator = page.locator(
          '[data-se="okta_verify-totp"] a, [data-se="google_otp"] a,'
          ' a:has-text("Inserir um código"), a:has-text("Enter a code")'
      )
      if btn_fator.count() > 0 and btn_fator.first.is_visible():
        log_painel("📲 Selecionando opção de código Okta Verify...")
        btn_fator.first.click()
        time.sleep(2.5)
        continue

      campo_cred = page.locator(
          'input[name="credentials.passcode"], input[name="credentials.totp"],'
          ' input[name="answer"], input[type="password"], input[type="tel"],'
          ' input[type="text"]'
      )
      if campo_cred.count() > 0:
        input_alvo = None
        for i in range(campo_cred.count()):
          el = campo_cred.nth(i)
          nome_attr = (el.get_attribute("name") or "").lower()
          if el.is_visible() and nome_attr != "identifier":
            input_alvo = el
            break

        if input_alvo:
          tipo_input = (input_alvo.get_attribute("type") or "text").lower()
          auto_attr = (input_alvo.get_attribute("autocomplete") or "").lower()

          titulo_form = ""
          try:
            titulo_el = page.locator(
                'h2.okta-form-title, [data-se="o-form-head"], label[for]'
            ).first
            if titulo_el.count() > 0:
              titulo_form = (titulo_el.text_content() or "").lower()
          except Exception:
            pass

          is_tela_codigo = (
              "verify" in titulo_form
              or "código" in titulo_form
              or "code" in titulo_form
              or tipo_input == "tel"
              or auto_attr == "one-time-code"
          )
          is_tela_senha = tipo_input == "password" and not is_tela_codigo

          if is_tela_senha and not codigo_recebido:
            if (time.time() - ultima_senha_preenchida) > 6:
              log_painel(
                  "🔑 Tela de palavra-passe detectada! Inserindo senha"
                  " automaticamente..."
              )
              input_alvo.fill("")
              input_alvo.fill(senha_meli_atual)
              clicar_botao_avancar_okta(page)
              ultima_senha_preenchida = time.time()
              log_painel(
                  "⏳ Palavra-passe enviada! Aguardando redirecionamento..."
              )
              time.sleep(4)
              continue
          else:
            informar_status_login("AGUARDANDO_OKTA")
            if not aviso_okta_enviado:
              log_painel(
                  "📲 AGUARDANDO CÓDIGO OKTA: Digite os 6 dígitos na 'Área do"
                  " Robô' e clique em Ativar!"
              )
              aviso_okta_enviado = True
              inicio_espera_okta = time.time()

            codigo_para_usar = (
                codigo_recebido or gerar_codigo_totp_automatico()
            )
            if codigo_para_usar:
              log_painel(
                  f"🚀 Inserindo código Okta ({codigo_para_usar}) e"
                  " avançando..."
              )
              input_alvo.fill("")
              input_alvo.fill(codigo_para_usar)
              clicar_botao_avancar_okta(page)
              aviso_okta_enviado = False
              inicio_espera_okta = time.time()
              time.sleep(4)
              continue

    except Exception:
      pass

    time.sleep(1.5)

  time.sleep(3)
  salvar_ultima_conta_logada(CONTA_ATUAL)
  informar_status_login("LOGADO")
  log_painel(
      "✅ Login concluído com sucesso! Sessão ativa e salva por um período."
  )
  return True


def traduzir_status_meli(status_bruto: str, coletados_int: int) -> str:
  """Converte os status do Mercado Livre para o padrão oficial da planilha JM"""
  st = str(status_bruto or "").strip().upper()
  if "SEM INÍCIO" in st or "SEM INICIO" in st or st == "PENDENTE DE LOGIN":
    return "PENDENTE DE LOGIN"
  if (
      "ANDAMENTO" in st
      or "INÍCIO" in st
      or "INICIO" in st
      or st in ["LOGADO", "COLETANDO"]
  ):
    return "COLETANDO" if coletados_int > 0 else "LOGADO"
  if "CONCLU" in st or st in ["FINALIZADA", "FINALIZADO", "CANCELADA"]:
    return "FINALIZADA" if coletados_int > 0 else "CANCELADA"
  return st if st else "PENDENTE DE LOGIN"


# ==========================================
# MÓDULO 1: RASPAGEM DE MONITORAMENTO (ALTA PRECISÃO)
# ==========================================
def aguardar_carregamento_meli(page, tempo_max=15):
  """Aguarda os spinners e skeletons do Mercado Livre sumirem após limpar ou aplicar filtros"""
  time.sleep(1.2)
  fim = time.time() + tempo_max
  while time.time() < fim:
    pulsar_watchdog()
    try:
      spinners = page.locator(
          ".andes-spinner:visible, .andes-skeleton:visible,"
          ' [aria-busy="true"]:visible'
      ).count()
      if spinners == 0:
        break
    except Exception:
      break
    time.sleep(0.5)
  time.sleep(1.0)


def clicar_limpar_todos_filtros(page):
  """Clica no botão 'Limpar todos' usando os seletores nativos do Playwright"""
  pulsar_watchdog()
  seletores_limpar = [
      'button.button-link__button:has(span:has-text("Limpar todos"))',
      'span.andes-typography:has-text("Limpar todos")',
      "text=Limpar todos",
  ]
  for sel in seletores_limpar:
    try:
      btn = page.locator(sel).first
      if btn.count() > 0 and btn.is_visible():
        log_painel("🧹 Limpando todos os filtros da tela do Mercado Livre...")
        btn.click(timeout=8000)
        aguardar_carregamento_meli(page, tempo_max=12)
        return True
    except Exception:
      try:
        btn = page.locator(sel).first
        if btn.count() > 0 and btn.is_visible():
          btn.click(force=True, timeout=5000)
          aguardar_carregamento_meli(page, tempo_max=12)
          return True
      except Exception:
        pass
  return False


def garantir_deposito_sem_filtro(page):
  """Garante que nenhuma tag de HUB anterior ficou presa quando a tarefa é TODOS"""
  for _ in range(3):
    pulsar_watchdog()
    try:
      combo = page.locator(
          'button.compact-selector__trigger[aria-label="Depósito"],'
          ' button[role="combobox"][aria-label="Depósito"]'
      ).first
      if combo.count() == 0:
        return True
      tags = [
          t.strip()
          for t in combo.locator(".andes-tag__label").all_inner_texts()
          if t.strip()
      ]
      if not tags:
        return True

      log_painel(
          f"⚠️ Removendo filtro anterior residual ({', '.join(tags)}) para"
          " liberar leitura Global..."
      )
      clicar_limpar_todos_filtros(page)

      tags_ainda = [
          t.strip()
          for t in combo.locator(".andes-tag__label").all_inner_texts()
          if t.strip()
      ]
      if tags_ainda:
        page.evaluate("""() => {
                    const botoesFecharTag = document.querySelectorAll('button[aria-label="Depósito"] .andes-tag__close, .andes-tag__close');
                    botoesFecharTag.forEach(b => b.click());
                }""")
        aguardar_carregamento_meli(page, tempo_max=12)
    except Exception:
      break
  return True


def obter_total_rotas_painel_meli(page) -> int:
  """Lê o número exato de rotas no card de estatísticas: <div class='stats__item--routes'>...<p class='item-content__value'>242</p>"""
  pulsar_watchdog()
  try:
    total = page.evaluate("""() => {
            // 1. Seletor exato da caixa de Rotas (Print 2)
            const elRoutes = document.querySelector('.stats__item--routes p.item-content__value');
            if (elRoutes) {
                const txt = (elRoutes.textContent || '').replace(/\\D/g, '');
                if (txt !== '') return parseInt(txt, 10);
            }

            // 2. Fallback procurando o bloco .item-content cujo título seja 'Rotas'
            const blocos = Array.from(document.querySelectorAll('.stats .item-content, .stats__item .item-content'));
            for (const b of blocos) {
                const ps = b.querySelectorAll('p');
                if (ps.length >= 2) {
                    const titulo = (ps[0].textContent || '').trim().toLowerCase();
                    if (titulo === 'rotas') {
                        const val = (ps[1].textContent || '').replace(/\\D/g, '');
                        if (val !== '') return parseInt(val, 10);
                    }
                }
            }

            // 3. Fallback primeiro p.item-content__value dentro de .stats
            const elFirst = document.querySelector('.stats p.item-content__value');
            if (elFirst) {
                const txt = (elFirst.textContent || '').replace(/\\D/g, '');
                if (txt !== '') return parseInt(txt, 10);
            }
            return -1;
        }""")
    return int(total) if total is not None else -1
  except Exception:
    return -1


def obter_assinatura_pagina_atual(page) -> str:
  """Retorna os primeiros e últimos IDs da página atual para confirmar que a paginação trocou os cards"""
  try:
    return (
        page.evaluate("""() => {
            const rows = Array.from(document.querySelectorAll('div.list > div.monitoring-row, div.monitoring-row'));
            if (rows.length === 0) return '';
            const ids = rows.map(r => {
                const el = r.querySelector('.button-copy__text');
                return el ? (el.textContent || '').replace(/[^0-9]/g, '') : '';
            }).filter(Boolean);
            if (ids.length === 0) return '';
            return `${ids.length}:${ids[0]}_${ids[ids.length - 1]}`;
        }""")
        or ""
    )
  except Exception:
    return ""


def extrair_cards_pagina_js(page, hub_alvo, data_hoje_str):
  """Extrai instantaneamente todas as rotas da página atual direto do DOM (baseado no HTML oficial do Meli)"""
  pulsar_watchdog()
  try:
    cards_brutos = page.evaluate(
        """(hubAlvo) => {
            const linhas = Array.from(document.querySelectorAll('div.list > div.monitoring-row, div.monitoring-row'));
            const resultados = [];

            for (const linha of linhas) {
                const higher = linha.querySelector('.monitoring-row-higher') || linha;
                const lower = linha.querySelector('.monitoring-row-lower') || linha;
                const textoCompleto = linha.textContent || '';

                // 1. ID da Rota (<p class="button-copy__text">Rota ID: 469604458</p>)
                let idBruto = '';
                const idEl = linha.querySelector('.button-copy__text');
                if (idEl) {
                    idBruto = idEl.textContent || '';
                } else {
                    const mId = textoCompleto.match(/Rota\\s*ID:\\s*(\\d+)/i);
                    if (mId) idBruto = mId[1];
                }
                const idRota = idBruto.replace(/[^0-9]/g, '');
                if (!idRota) continue;

                // 2. Nome da Rota (<p class="monitoring-row-higher-details__text bold">Rota BRXSP10_C13_DED_17</p>)
                const nomeEl = higher.querySelector('.main-data p.monitoring-row-higher-details__text.bold, p.monitoring-row-higher-details__text.bold');
                let nomeRota = nomeEl ? (nomeEl.textContent || '').replace(/^Rota\\s+/i, '').trim() : '';
                if (nomeRota.toUpperCase().includes('NÃO PLANEJADA') || nomeRota.toUpperCase().includes('NAO PLANEJADA')) {
                    const pref = (hubAlvo && hubAlvo !== 'TODOS') ? hubAlvo : 'AVULSA';
                    nomeRota = `${pref}_ROTA_AVULSA`;
                }

                // 3. Motorista ([data-testid="carrier-or-driver-name"] ou .monitoring-row-lower-details.driver p)
                let motorista = '';
                const motEl = higher.querySelector('[data-testid="carrier-or-driver-name"]') || lower.querySelector('.monitoring-row-lower-details.driver p');
                if (motEl) {
                    motorista = (motEl.textContent || '').trim();
                }

                // 4. Placa (.monitoring-row-lower-details.vehicle p -> "IQT4160 | HR")
                let placa = '';
                const placaEl = lower.querySelector('.monitoring-row-lower-details.vehicle p');
                if (placaEl) {
                    placa = (placaEl.textContent || '').split('|')[0].trim();
                }

                // 5. Horário (.monitoring-row-higher-details.eta p -> "ETA 13h36" ou "ATA: 11h30")
                let horario = 'Sem Horário';
                const horEl = higher.querySelector(".monitoring-row-higher-details.eta p, div[data-testid='active-route-eta'] p");
                if (horEl) {
                    const hTxt = (horEl.textContent || '').trim();
                    if (hTxt) horario = hTxt;
                }

                // 6. Pacotes Coletados e Estimados (.monitoring-row-higher-details.packages)
                let coletados = '0';
                let estimados = '0';
                const pkgBox = higher.querySelector('.monitoring-row-higher-details.packages');
                const textoPkg = pkgBox ? (pkgBox.textContent || '') : textoCompleto;

                const colEl = pkgBox ? pkgBox.querySelector('p.bold') : null;
                if (colEl) {
                    const cDig = (colEl.textContent || '').replace(/\\D/g, '');
                    if (cDig !== '') coletados = cDig;
                } else {
                    const mCol = textoPkg.match(/(\\d[\\d\\.]*)\\s*(?:pacotes\\s*)?coletados/i);
                    if (mCol) coletados = mCol[1].replace(/\\D/g, '');
                }

                const mEst = textoPkg.match(/(\\d[\\d\\.]*)\\s*(?:pacotes\\s*)?estimados/i);
                if (mEst) {
                    estimados = mEst[1].replace(/\\D/g, '');
                }

                // 7. Status (.monitoring-row-higher-details.status-main p.bold)
                const stEl = higher.querySelector('.status-main p.bold, .monitoring-row-higher-details.status p.bold');
                const statusBruto = stEl ? (stEl.textContent || '').trim() : 'Sem início';

                resultados.push({
                    id_rota: idRota,
                    nome_rota: nomeRota,
                    motorista: motorista,
                    placa: placa,
                    horario: horario,
                    coletados: coletados,
                    estimados: estimados,
                    status_bruto: statusBruto
                });
            }
            return resultados;
        }""",
        hub_alvo,
    )

    formatados = []
    for item in cards_brutos or []:
      int_est = (
          int(item["estimados"]) if str(item["estimados"]).isdigit() else 0
      )
      int_col = (
          int(item["coletados"]) if str(item["coletados"]).isdigit() else 0
      )
      restante = str(int_est - int_col)
      status_traduzido = traduzir_status_meli(item["status_bruto"], int_col)
      match_cluster = re.search(r"_(C\d+)_", item["nome_rota"])
      cluster = match_cluster.group(1) if match_cluster else "Sem Cluster"

      formatados.append({
          "id_rota": item["id_rota"],
          "nome_rota": item["nome_rota"],
          "cluster": cluster,
          "horario": item["horario"],
          "motorista": item["motorista"],
          "placa": item["placa"],
          "status": status_traduzido,
          "estimado_total": str(int_est),
          "coletado_geral": str(int_col),
          "restante": restante,
          "data": data_hoje_str,
      })
    return formatados
  except Exception:
    return []


def avancar_botao_proximo_meli(page, assinatura_antes: str) -> bool:
  """Verifica se o botão <span class='andes-pagination__arrow-title'>Próximo</span> está disponível, clica e aguarda a nova página"""
  pulsar_watchdog()
  try:
    # Verifica no DOM se o botão Próximo existe e NÃO está desabilitado
    tem_proximo = page.evaluate("""() => {
            const spanProx = Array.from(document.querySelectorAll('span.andes-pagination__arrow-title')).find(
                el => (el.textContent || '').trim() === 'Próximo'
            );
            const liNext = document.querySelector('li.andes-pagination__button--next') || (spanProx ? spanProx.closest('li') : null);
            if (!liNext) return false;
            if (liNext.classList.contains('andes-pagination__button--disabled')) return false;

            const link = liNext.querySelector('a, button') || (spanProx ? spanProx.closest('a, button') : null);
            if (!link) return false;
            if (link.getAttribute('aria-disabled') === 'true' || link.disabled) return false;

            const rect = link.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        }""")

    if not tem_proximo:
      return False

    # Rola até o botão Próximo e clica
    seletores_proximo = [
        (
            "li.andes-pagination__button--next:not(.andes-pagination__button--disabled)"
            ' a[data-andes-pagination-control="next"]'
        ),
        (
            "li.andes-pagination__button--next:not(.andes-pagination__button--disabled)"
            ' a:has(span.andes-pagination__arrow-title:has-text("Próximo"))'
        ),
        (
            "li.andes-pagination__button--next:not(.andes-pagination__button--disabled)"
            " a"
        ),
        'span.andes-pagination__arrow-title:has-text("Próximo")',
    ]

    clicou = False
    for sel in seletores_proximo:
      loc = page.locator(sel).first
      if loc.count() > 0 and loc.is_visible():
        try:
          loc.scroll_into_view_if_needed()
          time.sleep(0.3)
          loc.click(timeout=8000)
          clicou = True
          break
        except Exception:
          try:
            loc.click(force=True, timeout=5000)
            clicou = True
            break
          except Exception:
            pass

    if not clicou:
      return False

    # Aguarda até 15s a lista trocar os IDs (garante que a nova página carregou de verdade)
    fim_espera = time.time() + 15.0
    while time.time() < fim_espera:
      pulsar_watchdog()
      time.sleep(0.4)
      spinners = page.locator(
          ".andes-spinner:visible, .andes-skeleton:visible,"
          ' [aria-busy="true"]:visible'
      ).count()
      if spinners > 0:
        continue
      nova_assinatura = obter_assinatura_pagina_atual(page)
      if nova_assinatura and nova_assinatura != assinatura_antes:
        time.sleep(0.5)
        return True

    aguardar_carregamento_meli(page, tempo_max=8)
    return True
  except Exception:
    return False


def salvar_rotas_no_banco(dados_raspados, hub_alvo):
  """Atualiza a tabela rotas_monitoramento preservando monitores, observações e edições manuais"""
  log_painel(
      f"📦 {hub_alvo}: {len(dados_raspados)} rota(s) capturada(s)! Atualizando"
      " planilha..."
  )
  df_novo = pd.DataFrame(dados_raspados)
  try:
    insp = inspect(motor_db)

    if insp.has_table("rotas_monitoramento"):
      df_antigo = pd.read_sql_table("rotas_monitoramento", con=motor_db)
      for col_extra in [
          "monitor",
          "status_manual",
          "restante",
          "ocorrencia",
          "revertido",
          "observacoes",
          "cores_customizadas",
      ]:
        if col_extra not in df_antigo.columns:
          df_antigo[col_extra] = ""

      df_antigo["id_rota"] = df_antigo["id_rota"].astype(str)
      df_novo["id_rota"] = df_novo["id_rota"].astype(str)

      df_antigo = df_antigo.drop_duplicates(subset=["id_rota"], keep="last")
      df_novo = df_novo.drop_duplicates(subset=["id_rota"], keep="last")

      data_hoje = pd.Timestamp.now().strftime("%d/%m/%Y")
      df_antigo_passado = df_antigo[df_antigo["data"] != data_hoje].copy()
      df_antigo_hoje = df_antigo[df_antigo["data"] == data_hoje].copy()

      mapa_monitores = {}
      for _, row_ant in df_antigo_hoje.iterrows():
        mon_val = str(row_ant.get("monitor", "") or "").upper().strip()
        clus_val = str(row_ant.get("cluster", "") or "").upper().strip()
        hub_val = (
            str(row_ant.get("nome_rota", "") or "").split("_")[0].upper().strip()
        )
        if mon_val and clus_val and clus_val != "SEM CLUSTER":
          mapa_monitores[(hub_val, clus_val)] = mon_val

      df_antigo_hoje.set_index("id_rota", inplace=True)
      df_novo.set_index("id_rota", inplace=True)

      indices_comuns = df_antigo_hoje.index.intersection(df_novo.index)
      for idx in indices_comuns:
        h_antigo = str(df_antigo_hoje.at[idx, "horario"]).upper().strip()
        h_novo = str(df_novo.at[idx, "horario"]).upper().strip()
        is_antigo_bom = (
            h_antigo not in ["SEM HORÁRIO", "NAN", ""]
            and "ATA" not in h_antigo
            and "ETA" not in h_antigo
        )
        is_novo_ruim = (
            "ATA" in h_novo
            or "ETA" in h_novo
            or h_novo in ["SEM HORÁRIO", ""]
        )
        if is_antigo_bom and is_novo_ruim:
          df_novo.at[idx, "horario"] = df_antigo_hoje.at[idx, "horario"]

        c_antigo = str(df_antigo_hoje.at[idx, "cluster"]).strip()
        c_novo = str(df_novo.at[idx, "cluster"]).strip()
        if (
            c_antigo
            and c_antigo.upper() != "SEM CLUSTER"
            and c_novo.upper() == "SEM CLUSTER"
        ):
          df_novo.at[idx, "cluster"] = c_antigo

        p_antiga = str(df_antigo_hoje.at[idx, "placa"]).strip()
        p_nova = str(df_novo.at[idx, "placa"]).strip()
        if p_antiga and p_antiga.upper() != "NAN" and not p_nova:
          df_novo.at[idx, "placa"] = p_antiga

        st_antigo = str(df_antigo_hoje.at[idx, "status"] or "").upper().strip()
        foi_manual = (
            str(df_antigo_hoje.at[idx, "status_manual"] or "").upper().strip()
            == "SIM"
        )
        status_exclusivo_manual = st_antigo in ["DECLÍNIO", "DECLINIO"]

        if foi_manual or status_exclusivo_manual:
          df_novo.at[idx, "status"] = st_antigo

      colunas_atualizar = [
          "nome_rota",
          "cluster",
          "status",
          "horario",
          "motorista",
          "placa",
          "estimado_total",
          "coletado_geral",
          "restante",
      ]
      col_presentes = [
          col
          for col in colunas_atualizar
          if col in df_antigo_hoje.columns and col in df_novo.columns
      ]

      df_antigo_hoje.update(df_novo[col_presentes])

      rotas_novas = df_novo[~df_novo.index.isin(df_antigo_hoje.index)].copy()

      if not rotas_novas.empty:
        monitores_novas = []
        for _, r_nova in rotas_novas.iterrows():
          h_n = (
              str(r_nova.get("nome_rota", "") or "")
              .split("_")[0]
              .upper()
              .strip()
          )
          c_n = str(r_nova.get("cluster", "") or "").upper().strip()
          monitores_novas.append(mapa_monitores.get((h_n, c_n), ""))
        rotas_novas["monitor"] = monitores_novas
        rotas_novas["status_manual"] = ""
        rotas_novas["ocorrencia"] = ""

      df_antigo_hoje.reset_index(inplace=True)
      rotas_novas.reset_index(inplace=True)

      df_final = pd.concat(
          [df_antigo_passado, df_antigo_hoje, rotas_novas], ignore_index=True
      )
      df_final.to_sql(
          "rotas_monitoramento", con=motor_db, if_exists="replace", index=False
      )
    else:
      df_novo["monitor"] = ""
      df_novo["status_manual"] = ""
      df_novo["ocorrencia"] = ""
      df_novo.to_sql(
          "rotas_monitoramento", con=motor_db, if_exists="replace", index=False
      )

  except Exception as e:
    log_painel(f"⚠️ Erro ao cruzar dados antigos: {e}")


def executar_raspagem(page, hub_alvo):
  mapa_acumulado_tentativas = {}

  for tentativa in range(2):
    try:
      log_painel(
          f"⚡ Iniciando processo de raspagem para {hub_alvo} (Tentativa"
          f" {tentativa+1}/2)..."
      )

      # Na 2ª tentativa (ou se estiver fora da página), recarrega a página do zero como solicitado
      if tentativa > 0 or "first-mile/monitoring" not in (page.url or ""):
        page.goto(
            "https://envios.adminml.com/logistics/first-mile/monitoring",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        aguardar_carregamento_meli(page, tempo_max=15)
      else:
        time.sleep(2)

      if pagina_esta_no_okta(page):
        log_painel("🔐 Sessão pediu reautenticação. Verificando login...")
        garantir_login(page)
        page.goto(
            "https://envios.adminml.com/logistics/first-mile/monitoring",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        aguardar_carregamento_meli(page, tempo_max=15)

      # 1. Sempre limpa todos os filtros antes de começar
      clicar_limpar_todos_filtros(page)

      if hub_alvo != "TODOS":
        log_painel(f"📍 Selecionando depósito {hub_alvo} após limpar filtros...")
        combo_deposito = page.locator(
            'button.compact-selector__trigger[aria-label="Depósito"],'
            ' button[role="combobox"][aria-label="Depósito"]'
        ).first
        combo_deposito.wait_for(state="visible", timeout=20000)

        if combo_deposito.get_attribute("aria-expanded") != "true":
          combo_deposito.click(timeout=10000)
          time.sleep(1.5)

        campo_busca = page.locator(
            "input.andes-form-control__field[data-andes-searchbox-input='true'],"
            " input[placeholder='Buscar']"
        ).first
        campo_busca.wait_for(state="visible", timeout=10000)
        campo_busca.fill("")
        time.sleep(0.3)
        campo_busca.fill(hub_alvo)
        time.sleep(1.5)

        opcao_hub = page.locator(
            f'li[role="option"][data-key="{hub_alvo}"],'
            f' li[role="option"]:has-text("{hub_alvo}")'
        ).first
        if opcao_hub.count() == 0 or not opcao_hub.is_visible():
          log_painel(
              f"❌ ERRO: O depósito '{hub_alvo}' não existe na lista do Mercado"
              " Livre! Verifique a digitação."
          )
          page.keyboard.press("Escape")
          return "HUB_INVALIDO"

        if opcao_hub.get_attribute("aria-selected") != "true":
          opcao_hub.click(timeout=5000)
          time.sleep(0.8)
          if opcao_hub.get_attribute("aria-selected") != "true":
            chk_box = opcao_hub.locator(
                '.andes-checkbox__checkbox, input[type="checkbox"]'
            ).first
            if chk_box.count() > 0:
              chk_box.click(force=True)
              time.sleep(0.8)

        btn_aplicar = page.locator(
            '.compact-selector__footer button:has-text("Aplicar"),'
            ' button:has(span[data-andes-button-content="true"]:has-text("Aplicar"))'
        ).first
        if (
            btn_aplicar.count() > 0
            and btn_aplicar.is_visible()
            and not btn_aplicar.is_disabled()
        ):
          btn_aplicar.click(timeout=8000)
          aguardar_carregamento_meli(page, tempo_max=15)
        else:
          page.keyboard.press("Escape")
          time.sleep(2)

        tags_aplicadas = [
            t.strip().upper()
            for t in combo_deposito.locator(
                ".andes-tag__label"
            ).all_inner_texts()
        ]
        if hub_alvo.upper() not in tags_aplicadas:
          log_painel(
              f"⚠️ Filtro {hub_alvo} não fixou na 1ª tentativa (tags atuais:"
              f" {tags_aplicadas}). Repetindo seleção..."
          )
          if tentativa == 0:
            continue
      else:
        garantir_deposito_sem_filtro(page)
        log_painel(
            "🌎 Filtros limpos! Raspando TODAS as rotas disponíveis no sistema."
        )

      # Aguarda estabilizar o contador de rotas (<p class="item-content__value">)
      aguardar_carregamento_meli(page, tempo_max=10)
      total_esperado = obter_total_rotas_painel_meli(page)
      if total_esperado >= 0:
        log_painel(
            f"📊 Painel do Mercado Livre informa: {total_esperado} rota(s) para"
            f" {hub_alvo}."
        )

      if total_esperado == 0:
        if tentativa == 0:
          log_painel(
              f"⏳ {hub_alvo}: Painel indica 0 rotas na 1ª verificação."
              " Realizando confirmação de segurança..."
          )
          continue
        log_painel(
            f"ℹ️ {hub_alvo}: Nenhuma rota encontrada na tela após confirmação"
            " (0 rotas)."
        )
        return True

      parar_aviso_raspagem = threading.Event()

      def loop_aviso_raspando():
        log_painel("🤖 Robô raspando rotas...")
        while not parar_aviso_raspagem.wait(30.0):
          log_painel("🤖 Robô raspando rotas...")

      thread_aviso = threading.Thread(target=loop_aviso_raspando, daemon=True)
      thread_aviso.start()

      mapa_rotas_tentativa = {}
      pagina_atual = 1
      seletor_cards = "div.list > div.monitoring-row, div.monitoring-row"
      data_hoje_str = pd.Timestamp.now().strftime("%d/%m/%Y")

      try:
        while pagina_atual <= 150:
          pulsar_watchdog()
          try:
            page.wait_for_selector(seletor_cards, timeout=18000)
          except Exception:
            break

          # Rola até o final da lista e volta ao topo rápido para garantir que todos os elementos da página hidrataram
          try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.4)
          except Exception:
            pass

          assinatura_antes = obter_assinatura_pagina_atual(page)
          lote_pagina = extrair_cards_pagina_js(page, hub_alvo, data_hoje_str)

          for item in lote_pagina:
            mapa_rotas_tentativa[item["id_rota"]] = item
            mapa_acumulado_tentativas[item["id_rota"]] = item

          # Avança clicando em <span class="andes-pagination__arrow-title">Próximo</span>
          # Quando não existir mais ou estiver desabilitado, encerra a paginação!
          if avancar_botao_proximo_meli(page, assinatura_antes):
            pagina_atual += 1
            continue
          break
      finally:
        parar_aviso_raspagem.set()

      qtd_capturada = len(mapa_rotas_tentativa)

      # Verifica se a quantidade raspada bate com <p class="item-content__value">242</p>
      if total_esperado > 0 and qtd_capturada < total_esperado:
        if tentativa == 0:
          log_painel(
              "⚠️ Quantidade raspada divergente"
              f" ({qtd_capturada}/{total_esperado}), realizando outra tentativa"
          )
          continue
        else:
          log_painel(
              "⚠️ Quantidade de rotas divergentes em 2 tentativas"
              f" ({len(mapa_acumulado_tentativas)}/{total_esperado}),"
              " atualizando planilha com a quantidade raspada"
          )
          dados_finais = list(mapa_acumulado_tentativas.values())
          if dados_finais:
            salvar_rotas_no_banco(dados_finais, hub_alvo)
          return True

      # Quantidade bateu perfeitamente (ou superou caso tenha entrado rota nova durante a leitura)
      dados_finais = list(mapa_rotas_tentativa.values())
      if dados_finais:
        salvar_rotas_no_banco(dados_finais, hub_alvo)
        return True
      else:
        if tentativa == 0:
          log_painel(
              "⚠️ Quantidade raspada divergente, realizando outra tentativa"
          )
          continue
        log_painel(
            f"ℹ️ {hub_alvo}: Nenhuma rota encontrada na tela após confirmação"
            " (0 rotas)."
        )
        return True

    except Exception as e:
      detalhe_erro = (
          str(e).splitlines()[0] if str(e) else "Erro desconhecido"
      )
      log_painel(
          f"⚠️ Erro na tentativa {tentativa+1} ({hub_alvo}): {detalhe_erro}"
      )
      time.sleep(3)

  # Se chegou aqui após 2 tentativas mas havia capturado rotas parciais, salva o que capturou
  if mapa_acumulado_tentativas:
    log_painel(
        "⚠️ Quantidade de rotas divergentes em 2 tentativas, atualizando"
        " planilha com a quantidade raspada"
    )
    salvar_rotas_no_banco(list(mapa_acumulado_tentativas.values()), hub_alvo)
    return True

  return False


# ==========================================
# MÓDULO 2: DOWNLOAD DE RELATÓRIO
# ==========================================
def executar_relatorio(page, lista_hubs, ticket):
  for tentativa in range(2):
    try:
      pulsar_watchdog()
      log_painel(
          f"📥 [RELATÓRIO] Extraindo dados para: {', '.join(lista_hubs)}"
      )
      if not os.path.exists(PASTA_TEMP):
        os.makedirs(PASTA_TEMP, exist_ok=True)

      page.goto(
          "https://envios.adminml.com/logistics/reports/first-mile/reports-collections/query",
          wait_until="domcontentloaded",
          timeout=60000,
      )
      time.sleep(5)

      if pagina_esta_no_okta(page):
        log_painel(
            "🔐 Sessão pediu reautenticação ao abrir relatório. Verificando"
            " login..."
        )
        garantir_login(page)
        page.goto(
            "https://envios.adminml.com/logistics/reports/first-mile/reports-collections/query",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        time.sleep(5)

      regras_str = ticket.get("regras", "{}")
      try:
        regras = (
            json.loads(regras_str)
            if isinstance(regras_str, str)
            else regras_str
        )
      except Exception:
        regras = {}

      exportar_local = regras.get("exportar_local", "sistema")

      log_painel("📅 Selecionando filtro de data: Hoje")
      try:
        campo_data = page.locator("#input-date-rangeweek").first
        campo_data.wait_for(state="visible", timeout=15000)
        campo_data.click()
        time.sleep(1.2)

        btn_hoje = page.locator('td[data-today="true"] button').first
        if btn_hoje.count() > 0 and btn_hoje.is_visible():
          btn_hoje.click()
          time.sleep(0.5)
        else:
          hoje_str = datetime.datetime.now().strftime("%Y-%m-%d")
          btn_fallback = page.locator(f'td[data-day="{hoje_str}"] button').first
          if btn_fallback.count() > 0 and btn_fallback.is_visible():
            btn_fallback.click()
          time.sleep(0.5)

        btn_aplicar_data = page.locator(
            'span[data-andes-button-content="true"]:has-text("Aplicar")'
        ).first
        if btn_aplicar_data.count() > 0 and btn_aplicar_data.is_visible():
          btn_aplicar_data.click()
        time.sleep(1.5)
      except Exception as e:
        log_painel(f"⚠️ Aviso ao selecionar data: {str(e).splitlines()[0]}")

      if "TODOS" not in lista_hubs:
        marcados = 0
        for h in lista_hubs:
          label_hub = page.locator(f'label.andes-checkbox:has-text("{h}")').first
          if label_hub.count() > 0 and label_hub.is_visible():
            label_hub.locator('input[type="checkbox"]').check(force=True)
            marcados += 1
            time.sleep(0.5)
          else:
            log_painel(
                f"⚠️ HUB {h} ignorado (Não encontrado na lista de depósitos)."
            )

        if marcados == 0:
          log_painel("❌ Erro: Nenhum HUB válido para relatório.")
          return False
      else:
        log_painel("🌎 Extraindo relatório Global (Sem filtro de depósito).")

      btn_ver = page.locator(
          'span[data-andes-button-content="true"]:has-text("Ver resultados")'
      ).first
      if btn_ver.count() > 0 and btn_ver.is_visible():
        btn_ver.click()

      log_painel(
          "⏳ Aguardando processamento do relatório no Mercado Livre..."
      )
      botao_baixar = page.locator(
          'button[data-andes-button="true"]:has(span:has-text("Baixar"))'
      ).first

      habilitou_baixar = False
      for _ in range(15):
        pulsar_watchdog()
        time.sleep(2)
        if (
            botao_baixar.count() > 0
            and botao_baixar.is_visible()
            and not botao_baixar.is_disabled()
        ):
          habilitou_baixar = True
          break
        if (
            page.locator(
                "text=Não foram encontrados resultados, text=Sem resultados"
            ).count()
            > 0
        ):
          break

      if not habilitou_baixar:
        log_painel(
            "📭 Não há resultados de paradas disponíveis para"
            f" ({', '.join(lista_hubs)}) após aguardar carregamento."
        )
        return True

      pulsar_watchdog()
      with page.expect_download(timeout=60000) as download_info:
        botao_baixar.click()

      caminho_arquivo = os.path.join(
          PASTA_TEMP, download_info.value.suggested_filename
      )
      download_info.value.save_as(caminho_arquivo)

      try:
        pulsar_watchdog()
        if caminho_arquivo.endswith(".csv"):
          try:
            df = pd.read_csv(caminho_arquivo, encoding="utf-8")
          except Exception:
            df = pd.read_csv(
                caminho_arquivo, sep=None, engine="python", encoding="latin1"
            )
        else:
          df = pd.read_excel(caminho_arquivo)

        insp = inspect(motor_db)
        df.columns = df.columns.str.strip()

        if insp.has_table("rotas_brutas"):
          try:
            df_antigo_bruto = pd.read_sql_table("rotas_brutas", con=motor_db)
            if "data_extracao" in df_antigo_bruto.columns:
              df_antigo_bruto.drop(columns=["data_extracao"], inplace=True)

            df_antigo_bruto.columns = df_antigo_bruto.columns.str.strip()

            if (
                "ID do Rota" in df.columns
                and "ID do Rota" in df_antigo_bruto.columns
            ):
              ids_rotas_novas = set(df["ID do Rota"].astype(str).str.strip())
              df_antigo_preservado = df_antigo_bruto[
                  ~df_antigo_bruto["ID do Rota"]
                  .astype(str)
                  .str.strip()
                  .isin(ids_rotas_novas)
              ]
              df_combinado = pd.concat(
                  [df_antigo_preservado, df], ignore_index=True
              )
            else:
              df_combinado = df

            df_combinado.to_sql(
                "rotas_brutas", con=motor_db, if_exists="replace", index=False
            )
            log_painel(
                f"✅ Relatório sincronizado ({len(df)} linhas processadas)."
            )
          except Exception as e:
            log_painel(
                f"⚠️ Aviso: Conflito ao mesclar histórico ({e}). Substituindo"
                " tabela pela extração atual."
            )
            df.to_sql(
                "rotas_brutas", con=motor_db, if_exists="replace", index=False
            )
        else:
          df.to_sql(
              "rotas_brutas", con=motor_db, if_exists="replace", index=False
          )

        df_hist = df.copy()
        df_hist["data_extracao"] = pd.Timestamp.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        if insp.has_table("relatorios_historico"):
          colunas_banco = [
              c["name"] for c in insp.get_columns("relatorios_historico")
          ]
          colunas_novas = [
              c for c in df_hist.columns if c not in colunas_banco
          ]

          if colunas_novas:
            try:
              with motor_db.begin() as conn:
                for c in colunas_novas:
                  conn.execute(
                      text(
                          "ALTER TABLE relatorios_historico ADD COLUMN"
                          f' "{c}" TEXT;'
                      )
                  )
              log_painel(
                  "🔄 Estrutura do histórico atualizada com"
                  f" {len(colunas_novas)} nova(s) coluna(s)."
              )
            except Exception as e_alter:
              log_painel(
                  f"⚠️ Aviso ao adaptar colunas do histórico: {e_alter}"
              )

        df_hist.to_sql(
            "relatorios_historico",
            con=motor_db,
            if_exists="append",
            index=False,
        )
      except Exception as e:
        log_painel(f"⚠️ Erro ao processar arquivo no DB: {e}")

      if exportar_local == "local":
        try:
          pasta_export = os.path.join(PASTA_TEMP, "downloads_pc")
          os.makedirs(pasta_export, exist_ok=True)

          extensao = ".csv" if caminho_arquivo.endswith(".csv") else ".xlsx"
          prefixo = "Global" if "TODOS" in lista_hubs else lista_hubs[0]
          nome_pc = (
              f"Relatorio_{prefixo}_"
              f"{datetime.datetime.now().strftime('%d%m%Y_%H%M%S')}{extensao}"
          )
          shutil.copy(caminho_arquivo, os.path.join(pasta_export, nome_pc))
          log_painel(
              f"💾 [DOWNLOAD_PC:{nome_pc}] Transferindo relatório para o seu"
              " computador..."
          )
        except Exception as e:
          log_painel(f"❌ Erro ao preparar download para o PC: {e}")

      if os.path.exists(caminho_arquivo):
        os.remove(caminho_arquivo)

      return True

    except Exception as e:
      detalhe = str(e).splitlines()[0] if str(e) else "Erro desconhecido"
      log_painel(
          f"⚠️ Erro de página no relatório (Tentativa {tentativa+1}): {detalhe}"
      )
      time.sleep(3)

  return False


# ==========================================
# CÉREBRO DO ORQUESTRADOR
# ==========================================
def iniciar_orquestrador():
  threading.Thread(target=daemon_heartbeat, daemon=True).start()

  args_servidor_leve = [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      "--disable-software-rasterizer",
      "--disable-extensions",
      "--disable-background-networking",
      "--disable-sync",
      "--metrics-recording-only",
      "--mute-audio",
  ]

  primeiro_boot = True

  while True:
    try:
      pulsar_watchdog()
      limpar_travas_sessao()
      with sync_playwright() as p:
        modo_str = (
            "HEADLESS (Servidor)"
            if HEADLESS_MODE
            else "INTERFACE VISÍVEL (Local)"
        )
        if primeiro_boot:
          log_painel(f"🤖 ROBÔ INICIADO | Modo: {modo_str}")
          primeiro_boot = False

        navegador = p.chromium.launch_persistent_context(
            user_data_dir=PASTA_SESSAO,
            headless=HEADLESS_MODE,
            args=args_servidor_leve,
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            accept_downloads=True,
        )
        page = navegador.pages[0] if navegador.pages else navegador.new_page()
        estado_anterior = ""
        tarefas_desde_ultimo_reset = 0
        page.set_default_timeout(30000)
        page.set_default_navigation_timeout(60000)

        garantir_login(page, forcar_recarregamento=True)

        while True:
          try:
            pulsar_watchdog()
            cmd_standby = ler_comando_okta_api()
            if cmd_standby.get("reiniciar_login"):
              log_painel(
                  "🔄 Comando de recarregamento recebido! Reiniciando"
                  " verificação de login..."
              )
              garantir_login(page, forcar_recarregamento=True)
              estado_anterior = ""

            df_tickets = pd.read_sql_table("tickets_robo", con=motor_db)
            ticket_processado = False

            ativos = df_tickets[df_tickets["status"] == "ATIVO"].copy()
            qtd_ativos = len(ativos)
            nomes_fila = [
                f"{r['hub']} ({r['tipo'].upper()})"
                for _, r in ativos.iterrows()
            ]

            prontos_para_rodar = []
            for index, row in ativos.iterrows():
              t_dict = row.to_dict()
              if avaliar_se_deve_rodar(t_dict):
                prioridade = 0 if t_dict.get("tipo") == "avulso" else 1
                prontos_para_rodar.append((prioridade, index, t_dict))

            prontos_para_rodar.sort(key=lambda x: (x[0], x[1]))

            for pos_fila, (_, _, ticket) in enumerate(
                prontos_para_rodar, start=1
            ):
              garantir_login(page, forcar_recarregamento=False)

              hub_string = ticket["hub"]
              acao = ticket.get("acao", "monitoramento")
              tipo_ticket = ticket.get("tipo", "avulso")
              lista_hubs = [h.strip() for h in hub_string.split(",")]

              restantes_prontos = len(prontos_para_rodar) - pos_fila
              log_painel(
                  f"🚨 EXECUTANDO ({pos_fila}º da Fila): {hub_string}"
                  f" [{tipo_ticket.upper()}] | Ação: {acao.upper()}"
              )
              if restantes_prontos > 0:
                prox_nomes = ", ".join([
                    f"{ idx_p + 1 }º {tk['hub']} ({tk['acao'].upper()})"
                    for idx_p, (_, _, tk) in enumerate(
                        prontos_para_rodar[pos_fila:], start=pos_fila
                    )
                ])
                log_painel(f"📋 Próximos na ordem imediata: {prox_nomes}")
              estado_anterior = "trabalhando"

              try:
                sucesso_geral = True
                if acao == "monitoramento":
                  hub_invalido = False
                  for h in lista_hubs:
                    res = executar_raspagem(page, h)
                    if res == "HUB_INVALIDO":
                      hub_invalido = True
                      sucesso_geral = False
                    elif not res:
                      sucesso_geral = False
                  if sucesso_geral:
                    log_painel(
                        f"✅ PROCESSO CONCLUÍDO para: {hub_string}"
                        f" ({tipo_ticket.upper()})"
                    )
                  elif hub_invalido and tipo_ticket == "avulso":
                    log_painel(
                        f"🗑️ Removendo ticket avulso inválido ({hub_string}) da"
                        " fila."
                    )
                    with motor_db.begin() as conn:
                      conn.execute(
                          text(
                              "DELETE FROM tickets_robo WHERE hub = :h AND"
                              " acao = :a AND tipo = 'avulso'"
                          ),
                          {"h": hub_string, "a": acao},
                      )
                    ticket_processado = True
                    break

                elif acao == "relatorio":
                  sucesso_geral = executar_relatorio(page, lista_hubs, ticket)
                  if sucesso_geral:
                    log_painel(
                        "✅ RELATÓRIO BAIXADO | EXTRAÇÃO DE RELATÓRIO"
                        f" CONCLUÍDA para: {hub_string}"
                        f" ({tipo_ticket.upper()})"
                    )

                if sucesso_geral:
                  nova_coleta = datetime.datetime.now().strftime(
                      "%Y-%m-%d %H:%M:%S"
                  )
                  novo_status = (
                      "CONCLUIDO" if tipo_ticket == "avulso" else "ATIVO"
                  )

                  with motor_db.begin() as conn:
                    conn.execute(
                        text("""
                                            UPDATE tickets_robo 
                                            SET ultima_coleta = :uc, status = :st 
                                            WHERE hub = :h AND acao = :a AND tipo = :t
                                        """),
                        {
                            "uc": nova_coleta,
                            "st": novo_status,
                            "h": hub_string,
                            "a": acao,
                            "t": tipo_ticket,
                        },
                    )

                  ticket_processado = True
                  tarefas_desde_ultimo_reset += 1
                  break
                elif tipo_ticket == "avulso":
                  log_painel(
                      f"⚠️ Finalizando ticket avulso ({hub_string}) após 2"
                      " tentativas sem sucesso para liberar a fila."
                  )
                  with motor_db.begin() as conn:
                    conn.execute(
                        text("""
                                            UPDATE tickets_robo 
                                            SET status = 'CONCLUIDO' 
                                            WHERE hub = :h AND acao = :a AND tipo = 'avulso'
                                        """),
                        {"h": hub_string, "a": acao},
                    )
                  ticket_processado = True
                  break

              except Exception as e:
                log_painel(f"❌ Erro crítico ao processar ticket: {e}")

            if not ticket_processado:
              pulsar_watchdog()
              if tarefas_desde_ultimo_reset >= 6:
                log_painel(
                    "🧹 Reciclando memória do navegador preventivamente..."
                )
                matar_chromium_zumbi()
                gc.collect()
                break

              if estado_anterior != "dormindo":
                nomes_str = ", ".join(nomes_fila) if nomes_fila else "Nenhum"
                log_painel(
                    f"💤 Esperando. {qtd_ativos} tarefa(s) na fila:"
                    f" [{nomes_str}]."
                )
                estado_anterior = "dormindo"
              time.sleep(4)

          except Exception as e_inner:
            pulsar_watchdog()
            log_painel(f"⚠️ Oscilação no loop interno: {e_inner}")
            time.sleep(5)

    except Exception as e_outer:
      pulsar_watchdog()
      matar_chromium_zumbi()
      log_painel(f"🔄 Reiniciando motor do navegador ({e_outer})...")
      time.sleep(5)


if __name__ == "__main__":
  iniciar_orquestrador()