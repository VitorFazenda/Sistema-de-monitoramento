import os
import time
import re
import json
import datetime
import threading
import shutil
import requests
import gc
import pandas as pd
from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine, text, inspect
from dotenv import load_dotenv

# ==========================================
# CAMINHOS ABSOLUTOS (PREPARADO PARA SERVIDOR LINUX E LOCAL)
# ==========================================
DIRETORIO_ROBO = os.path.dirname(os.path.abspath(__file__))
DIRETORIO_RAIZ = os.path.abspath(os.path.join(DIRETORIO_ROBO, '..'))
CAMINHO_ENV = os.path.join(DIRETORIO_RAIZ, 'backend', '.env')
PASTA_SESSAO = os.path.join(DIRETORIO_ROBO, 'sessao_meli')
PASTA_TEMP = os.path.join(DIRETORIO_RAIZ, 'temp')
ARQUIVO_ULTIMA_CONTA = os.path.join(DIRETORIO_ROBO, 'ultima_conta_meli.json')

load_dotenv(CAMINHO_ENV)

DB_URL = os.getenv("DATABASE_URL")
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
MELI_USER = os.getenv("MELI_USER", "ext_vifazend")
MELI_PASS = os.getenv("MELI_PASS", "Palido%2696%")
OKTA_SECRET = os.getenv("OKTA_SECRET", "").strip()
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "False").lower() in ("true", "1", "t")

# ==========================================
# MAPEAMENTO SEGURO DE CONTAS 
# ==========================================
CONTAS_MELI = {
    "Vitor.Martins": {
        "user": MELI_USER,
        "pass": MELI_PASS
    },
    "Larissa.Campos": {
        "user": os.getenv("MELI_USER_2", "ext_camplari"),
        "pass": os.getenv("MELI_PASS_2", "Ensalada@0533.")
    }
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
    print("❌ Erro Crítico: DATABASE_URL não encontrada. Verifique o seu ficheiro .env!")
    exit()

motor_db = create_engine(DB_URL, pool_pre_ping=True, pool_recycle=300)
DIAS_SEMANA = ["Segunda", "Terca", "Quarta", "Quinta", "Sexta", "Sabado", "Domingo"]

def log_painel(mensagem):
    print(mensagem, flush=True)
    try:
        requests.post(f"{API_URL}/enviar_log", json={"mensagem": mensagem}, timeout=3)
    except Exception:
        pass

def informar_status_login(status_str):
    try:
        requests.post(
            f"{API_URL}/status_login_robo",
            json={"status": status_str, "conta": CONTA_ATUAL},
            timeout=2
        )
    except Exception:
        pass

def ler_comando_okta_api():
    """Consulta na API se o utilizador enviou código Okta, trocou a conta ou pediu para recarregar"""
    try:
        r = requests.get(f"{API_URL}/obter_comando_okta", timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {"codigo": "", "reiniciar_login": False, "trocar_conta": "", "conta_ativa": CONTA_ATUAL}

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
            regras = json.loads(regras_str)
        except Exception:
            return False
            
        dia_hoje = DIAS_SEMANA[agora.weekday()]
        if dia_hoje not in regras:
            return False
            
        hora_atual_str = agora.strftime("%H:%M")
        ultima_coleta_str = str(ticket.get("ultima_coleta", "Aguardando"))
        ultima_coleta_dt = None
        if ultima_coleta_str not in ["Aguardando", "None"]:
            try:
                ultima_coleta_dt = datetime.datetime.strptime(ultima_coleta_str, "%Y-%m-%d %H:%M:%S")
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
    while True:
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
        'input[data-type="save"]'
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
        'a.js-cancel.cancelb',
        'a:has-text("Voltar à tela de login")',
        'a:has-text("Sign out")'
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
        if page.locator('input[name="identifier"], #okta-sign-in, [data-se="o-form"]').count() > 0:
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
            page.goto("https://envios.adminml.com/logistics/first-mile/monitoring", wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
    except Exception as e:
        log_painel(f"⚠️ Aviso ao carregar URL inicial: {str(e).splitlines()[0]}")
        time.sleep(3)

    if not pagina_esta_no_okta(page):
        informar_status_login("LOGADO")
        log_painel("✅ Sessão ativa. Não foi necessário realizar login.")
        return True

    log_painel("🔐 Autenticação necessária. Iniciando fluxo inteligente de Login Okta...")
    informar_status_login("AGUARDANDO_OKTA")

    aviso_okta_enviado = False
    ultimo_user_preenchido = 0
    ultima_senha_preenchida = 0
    inicio_espera_okta = time.time()

    while pagina_esta_no_okta(page):
        cmd = ler_comando_okta_api()

        # 1. Verifica se o utilizador trocou a conta no seletor (Ex: Vitor.Martins <-> Larissa.Campos)
        nova_conta = str(cmd.get("trocar_conta", "")).strip()
        if nova_conta and nova_conta in CONTAS_MELI:
            CONTA_ATUAL = nova_conta
            salvar_ultima_conta_logada(CONTA_ATUAL)
            informar_status_login("AGUARDANDO_OKTA")
            log_painel(f"🔄 Alternando para o login de {CONTA_ATUAL}. Clicando em 'Voltar à tela de login'...")
            
            clicou_voltar = clicar_voltar_tela_login_okta(page)
            if not clicou_voltar:
                try:
                    page.goto("https://envios.adminml.com/logistics/first-mile/monitoring", wait_until="domcontentloaded", timeout=60000)
                    time.sleep(4)
                except Exception:
                    pass

            aviso_okta_enviado = False
            ultimo_user_preenchido = 0
            ultima_senha_preenchida = 0
            inicio_espera_okta = time.time()
            continue

        # 2. Se clicou em "Recarregar Site / Reiniciar Login" OU se passaram 5 minutos (300s) sem enviar o código Okta
        tempo_esperando_okta = time.time() - inicio_espera_okta
        passou_5_minutos = (aviso_okta_enviado and tempo_esperando_okta >= 300)

        if cmd.get("reiniciar_login") or passou_5_minutos:
            if passou_5_minutos:
                log_painel("⏰ 5 minutos aguardando código Okta. Recarregando a página automaticamente...")
            else:
                log_painel("🔄 Reiniciando página de login do Meli a pedido do painel...")
                
            aviso_okta_enviado = False
            ultimo_user_preenchido = 0
            ultima_senha_preenchida = 0
            inicio_espera_okta = time.time()
            try:
                clicar_voltar_tela_login_okta(page)
                page.goto("https://envios.adminml.com/logistics/first-mile/monitoring", wait_until="domcontentloaded", timeout=60000)
                time.sleep(4)
            except Exception as e:
                log_painel(f"⚠️ Erro ao recarregar página: {e}")
            continue

        codigo_recebido = str(cmd.get("codigo", "")).strip()
        credenciais_atuais = CONTAS_MELI.get(CONTA_ATUAL, CONTAS_MELI["Vitor.Martins"])
        usuario_meli_atual = credenciais_atuais["user"]
        senha_meli_atual = credenciais_atuais["pass"]

        try:
            # 3. Campo de Utilizador (Identifier)
            campo_user = page.locator('input[name="identifier"]')
            if campo_user.count() > 0 and campo_user.first.is_visible():
                campos_cred_visiveis = page.locator('input[type="password"]:visible, input[name="credentials.passcode"]:visible, input[name="credentials.totp"]:visible').count()
                if campos_cred_visiveis == 0 and (time.time() - ultimo_user_preenchido) > 8:
                    log_painel(f"👤 Preenchendo utilizador ({usuario_meli_atual}) e avançando...")
                    campo_user.first.fill("")
                    campo_user.first.fill(usuario_meli_atual)
                    clicar_botao_avancar_okta(page)
                    ultimo_user_preenchido = time.time()
                    aviso_okta_enviado = False
                    inicio_espera_okta = time.time()
                    time.sleep(3.5)
                    continue

            # 4. Se aparecer lista de fatores de autenticação
            btn_fator = page.locator('[data-se="okta_verify-totp"] a, [data-se="google_otp"] a, a:has-text("Inserir um código"), a:has-text("Enter a code")')
            if btn_fator.count() > 0 and btn_fator.first.is_visible():
                log_painel("📲 Selecionando opção de código Okta Verify...")
                btn_fator.first.click()
                time.sleep(2.5)
                continue

            # 5. Campos de Senha ou Código Okta
            campo_cred = page.locator('input[name="credentials.passcode"], input[name="credentials.totp"], input[name="answer"], input[type="password"], input[type="tel"], input[type="text"]')
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
                        titulo_el = page.locator('h2.okta-form-title, [data-se="o-form-head"], label[for]').first
                        if titulo_el.count() > 0:
                            titulo_form = (titulo_el.text_content() or "").lower()
                    except Exception:
                        pass

                    is_tela_codigo = (
                        "verify" in titulo_form or "código" in titulo_form or "code" in titulo_form or
                        tipo_input == "tel" or auto_attr == "one-time-code"
                    )
                    is_tela_senha = (tipo_input == "password" and not is_tela_codigo)

                    if is_tela_senha and not codigo_recebido:
                        if (time.time() - ultima_senha_preenchida) > 6:
                            log_painel("🔑 Tela de palavra-passe detectada! Inserindo senha automaticamente...")
                            input_alvo.fill("")
                            input_alvo.fill(senha_meli_atual)
                            clicar_botao_avancar_okta(page)
                            ultima_senha_preenchida = time.time()
                            log_painel("⏳ Palavra-passe enviada! Aguardando redirecionamento...")
                            time.sleep(4)
                            continue
                    else:
                        informar_status_login("AGUARDANDO_OKTA")
                        if not aviso_okta_enviado:
                            log_painel("📲 AGUARDANDO CÓDIGO OKTA: Digite os 6 dígitos na 'Área do Robô' e clique em Ativar!")
                            aviso_okta_enviado = True
                            inicio_espera_okta = time.time()

                        codigo_para_usar = codigo_recebido or gerar_codigo_totp_automatico()
                        if codigo_para_usar:
                            log_painel(f"🚀 Inserindo código Okta ({codigo_para_usar}) e avançando...")
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
    log_painel("✅ Login concluído com sucesso! Sessão ativa e salva por um período.")
    return True

def traduzir_status_meli(status_bruto: str, coletados_int: int) -> str:
    """Converte os status do Mercado Livre para o padrão oficial da planilha JM"""
    st = str(status_bruto or "").strip().upper()
    if "SEM INÍCIO" in st or "SEM INICIO" in st or st == "PENDENTE DE LOGIN":
        return "PENDENTE DE LOGIN"
    if "ANDAMENTO" in st or "INÍCIO" in st or "INICIO" in st or st in ["LOGADO", "COLETANDO"]:
        return "COLETANDO" if coletados_int > 0 else "LOGADO"
    if "CONCLU" in st or st in ["FINALIZADA", "FINALIZADO", "CANCELADA"]:
        return "FINALIZADA" if coletados_int > 0 else "CANCELADA"
    return st if st else "PENDENTE DE LOGIN"

# ==========================================
# MÓDULO 1: RASPAGEM DE MONITORAMENTO (SCROLL PROGRESSIVO + PAGINAÇÃO COMPLETA)
# ==========================================
def aguardar_carregamento_meli(page, tempo_max=12):
    """Aguarda os spinners do Mercado Livre sumirem após limpar ou aplicar filtros"""
    time.sleep(1.5)
    fim = time.time() + tempo_max
    while time.time() < fim:
        try:
            spinners = page.locator('.andes-spinner:visible, [aria-busy="true"]:visible').count()
            if spinners == 0:
                break
        except Exception:
            break
        time.sleep(0.6)
    time.sleep(1.5)

def clicar_limpar_todos_filtros(page):
    """Clica no botão 'Limpar todos' do Mercado Livre uma única vez e aguarda estabilizar"""
    seletores_limpar = [
        'button.button-link__button:has(span:has-text("Limpar todos"))',
        'span.andes-typography:has-text("Limpar todos")',
        'text=Limpar todos'
    ]
    for sel in seletores_limpar:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                log_painel("🧹 Limpando todos os filtros da tela do Mercado Livre...")
                btn.click(timeout=8000)
                aguardar_carregamento_meli(page, tempo_max=10)
                return True
        except Exception:
            continue
    return False

def extrair_cards_visiveis_js(page, hub_alvo):
    """Lê todos os cards renderizados na página instantaneamente via JavaScript no navegador"""
    try:
        return page.evaluate("""(hubAlvo) => {
            const cards = Array.from(document.querySelectorAll('div.list > div.monitoring-row, div.monitoring-row'));
            const resultados = [];
            cards.forEach((linha) => {
                const higher = linha.querySelector('.monitoring-row-higher') || linha;
                const textoCompleto = linha.innerText || linha.textContent || '';

                const nomeEl = higher.querySelector('p.monitoring-row-higher-details__text.bold');
                let nomeRota = nomeEl ? (nomeEl.textContent || '').replace('Rota ', '').trim() : '';
                if (nomeRota.toUpperCase().includes('NÃO PLANEJADA') || nomeRota.toUpperCase().includes('NAO PLANEJADA')) {
                    const pref = (hubAlvo && hubAlvo !== 'TODOS') ? hubAlvo : 'AVULSA';
                    nomeRota = `${pref}_ROTA_AVULSA`;
                }

                const idEl = linha.querySelector('.button-copy__text');
                let idBruto = idEl ? (idEl.textContent || '') : '';
                if (!idBruto) {
                    const mId = textoCompleto.match(/Rota\\s*ID:\\s*(\\d+)/i);
                    if (mId) idBruto = mId[1];
                }
                const idRota = idBruto.replace(/[^0-9]/g, '');
                if (!idRota) return;

                const motEl = higher.querySelector('[data-testid="carrier-or-driver-name"]');
                const motorista = motEl ? (motEl.textContent || '').trim() : '';

                let placa = '';
                const placaEl = linha.querySelector('.monitoring-row-lower-details.vehicle p');
                if (placaEl) {
                    placa = (placaEl.textContent || '').split('|')[0].trim();
                }

                const horEl = higher.querySelector(".monitoring-row-higher-details.eta p, div[data-testid='active-route-eta'] p");
                const horario = horEl ? (horEl.textContent || '').trim() : 'Sem Horário';

                const mCol = textoCompleto.match(/(\\d[\\d\\.]*)\\s*(?:pacotes\\s*)?coletados/i);
                const coletados = mCol ? mCol[1].replace(/\\D/g, '') : '0';

                const mEst = textoCompleto.match(/(\\d[\\d\\.]*)\\s*(?:pacotes\\s*)?estimados/i);
                const estimados = mEst ? mEst[1].replace(/\\D/g, '') : '0';

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
            });
            return resultados;
        }""", hub_alvo)
    except Exception:
        return []

def obter_total_rotas_painel_meli(page) -> int:
    """Lê o contador oficial de Rotas no topo da página do Mercado Livre (ex: 191)"""
    try:
        total = page.evaluate("""() => {
            const stats = document.querySelector('.stats');
            if (!stats) return 0;
            const texto = stats.innerText || '';
            const m = texto.match(/Rotas[\\s\\n]+(\\d[\\d\\.]*)/i);
            if (m && m[1]) return parseInt(m[1].replace(/\\D/g, ''), 10) || 0;
            return 0;
        }""")
        return int(total or 0)
    except Exception:
        return 0

def obter_primeiro_id_rota_visivel(page) -> str:
    """Retorna o ID da primeira rota visível no topo da lista para confirmar mudança de página"""
    try:
        return page.evaluate("""() => {
            const el = document.querySelector('div.list .button-copy__text, div.monitoring-row .button-copy__text');
            return el ? (el.textContent || '').replace(/[^0-9]/g, '') : '';
        }""") or ""
    except Exception:
        return ""

def ir_para_proxima_pagina_meli(page, proxima_pag: int, primeiro_id_antes: str) -> bool:
    """Avança para a próxima página via JS e retorna False imediatamente se não houver nova página"""
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.8)

        clicou = page.evaluate("""(numPag) => {
            const nav = document.querySelector('nav[aria-label*="Pagina" i], ul.andes-pagination, .andes-pagination');
            if (!nav) return false;

            // 1. Tenta pelo botão 'Seguinte' / 'Próximo' (somente se não estiver desativado)
            const liNext = nav.querySelector('.andes-pagination__button--next');
            if (liNext && !liNext.classList.contains('andes-pagination__button--disabled')) {
                const alvo = liNext.querySelector('a, button') || liNext;
                if (alvo && alvo.getAttribute('aria-disabled') !== 'true' && !alvo.disabled) {
                    alvo.scrollIntoView({ block: 'center' });
                    alvo.click();
                    return true;
                }
            }

            // 2. Fallback: botão numérico da próxima página (ex: '2', '3', '4')
            const botoes = Array.from(nav.querySelectorAll('li.andes-pagination__button:not(.andes-pagination__button--disabled) a, li.andes-pagination__button:not(.andes-pagination__button--disabled) button'));
            for (const b of botoes) {
                const txt = (b.innerText || b.textContent || '').trim();
                if (txt === String(numPag)) {
                    b.scrollIntoView({ block: 'center' });
                    b.click();
                    return true;
                }
            }
            return false;
        }""", proxima_pag)

        if not clicou:
            return False

        # Aguarda até 10 segundos os cards da página nova aparecerem
        fim_espera = time.time() + 10
        while time.time() < fim_espera:
            time.sleep(0.6)
            novo_id = obter_primeiro_id_rota_visivel(page)
            spinners = page.locator('.andes-spinner:visible, .andes-skeleton:visible, [aria-busy="true"]:visible').count()
            if spinners == 0 and novo_id and novo_id != primeiro_id_antes:
                time.sleep(0.8)
                return True

        # Se o 1º ID não mudou, chegou na última página -> retorna False para encerrar na hora!
        return False
    except Exception:
        return False

def executar_raspagem(page, hub_alvo):
    for tentativa in range(2):
        try:
            log_painel(f"⚡ Iniciando processo de raspagem para {hub_alvo} (Tentativa {tentativa+1}/2)...")
            
            if tentativa > 0 or "first-mile/monitoring" not in (page.url or ""):
                page.goto(
                    "https://envios.adminml.com/logistics/first-mile/monitoring",
                    wait_until="domcontentloaded",
                    timeout=60000
                )
                aguardar_carregamento_meli(page, tempo_max=12)
            else:
                time.sleep(2)
            
            if pagina_esta_no_okta(page):
                log_painel("🔐 Sessão pediu reautenticação. Verificando login...")
                garantir_login(page)
                page.goto(
                    "https://envios.adminml.com/logistics/first-mile/monitoring",
                    wait_until="domcontentloaded",
                    timeout=60000
                )
                aguardar_carregamento_meli(page, tempo_max=12)

            # 1. Sempre limpa todos os filtros antes de começar
            clicar_limpar_todos_filtros(page)

            if hub_alvo != "TODOS":
                log_painel(f"📍 Selecionando depósito {hub_alvo} após limpar filtros...")
                combo_deposito = page.locator('button.compact-selector__trigger[aria-label="Depósito"], button[role="combobox"][aria-label="Depósito"]').first
                combo_deposito.wait_for(state="visible", timeout=20000)

                if combo_deposito.get_attribute("aria-expanded") != "true":
                    combo_deposito.click(timeout=10000)
                    time.sleep(1.5)

                campo_busca = page.locator('input.andes-form-control__field[data-andes-searchbox-input="true"], input[placeholder="Buscar"]').first
                campo_busca.wait_for(state="visible", timeout=10000)
                campo_busca.fill("")
                time.sleep(0.3)
                campo_busca.fill(hub_alvo)
                time.sleep(1.5)

                opcao_hub = page.locator(f'li[role="option"][data-key="{hub_alvo}"], li[role="option"]:has-text("{hub_alvo}")').first
                if opcao_hub.count() == 0 or not opcao_hub.is_visible():
                    log_painel(f"❌ ERRO: O depósito '{hub_alvo}' não existe na lista do Mercado Livre! Verifique a digitação.")
                    page.keyboard.press("Escape")
                    return "HUB_INVALIDO"

                if opcao_hub.get_attribute("aria-selected") != "true":
                    opcao_hub.click(timeout=5000)
                    time.sleep(0.8)
                    if opcao_hub.get_attribute("aria-selected") != "true":
                        chk_box = opcao_hub.locator('.andes-checkbox__checkbox, input[type="checkbox"]').first
                        if chk_box.count() > 0:
                            chk_box.click(force=True)
                            time.sleep(0.8)

                btn_aplicar = page.locator('.compact-selector__footer button:has-text("Aplicar"), button:has(span[data-andes-button-content="true"]:has-text("Aplicar"))').first
                if btn_aplicar.count() > 0 and btn_aplicar.is_visible() and not btn_aplicar.is_disabled():
                    btn_aplicar.click(timeout=8000)
                    aguardar_carregamento_meli(page, tempo_max=15)
                else:
                    page.keyboard.press("Escape")
                    time.sleep(2)

                tags_aplicadas = [t.strip().upper() for t in combo_deposito.locator('.andes-tag__label').all_inner_texts()]
                if hub_alvo.upper() not in tags_aplicadas:
                    log_painel(f"⚠️ Filtro {hub_alvo} não fixou na 1ª tentativa (tags atuais: {tags_aplicadas}). Repetindo seleção...")
                    if tentativa == 0:
                        continue
            else:
                log_painel("🌎 Filtros limpos! Raspando TODAS as rotas disponíveis no sistema.")

            # Exibe o aviso inicial e repete apenas a cada 60 segundos enquanto estiver raspando
            parar_aviso_raspagem = threading.Event()
            def loop_aviso_raspando():
                log_painel("🤖 Robô raspando rotas...")
                while not parar_aviso_raspagem.wait(60.0):
                    log_painel("🤖 Robô raspando rotas...")

            thread_aviso = threading.Thread(target=loop_aviso_raspando, daemon=True)
            thread_aviso.start()

            mapa_rotas_capturadas = {}
            pagina_atual = 1
            seletor_cards = "div.list div.monitoring-row, div.monitoring-row, div.monitoring-row-higher"
            data_hoje_str = pd.Timestamp.now().strftime('%d/%m/%Y')
            
            try:
                page.wait_for_selector(seletor_cards, timeout=20000)
                total_esperado = obter_total_rotas_painel_meli(page)

                while True:
                    primeiro_id_pagina = obter_primeiro_id_rota_visivel(page)
                    qtd_antes_pagina = len(mapa_rotas_capturadas)

                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(0.6)

                    for passo_scroll in range(15):
                        lote_visivel = extrair_cards_visiveis_js(page, hub_alvo)
                        for item in lote_visivel:
                            id_r = item["id_rota"]
                            int_est = int(item["estimados"]) if str(item["estimados"]).isdigit() else 0
                            int_col = int(item["coletados"]) if str(item["coletados"]).isdigit() else 0
                            restante = str(int_est - int_col)
                            status_traduzido = traduzir_status_meli(item["status_bruto"], int_col)
                            match_cluster = re.search(r'_(C\d+)_', item["nome_rota"])
                            cluster = match_cluster.group(1) if match_cluster else "Sem Cluster"

                            mapa_rotas_capturadas[id_r] = {
                                "id_rota": id_r,
                                "nome_rota": item["nome_rota"],
                                "cluster": cluster,
                                "horario": item["horario"],
                                "motorista": item["motorista"],
                                "placa": item["placa"],
                                "status": status_traduzido,
                                "estimado_total": str(int_est),
                                "coletado_geral": str(int_col),
                                "restante": restante,
                                "data": data_hoje_str
                            }

                        chegou_ao_fim = page.evaluate("""() => {
                            const lista = document.querySelector('div.list');
                            if (lista && lista.scrollHeight > lista.clientHeight + 20) {
                                lista.scrollBy(0, 650);
                            }
                            const antes = window.scrollY;
                            window.scrollBy(0, 650);
                            return (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 15) && (window.scrollY === antes);
                        }""")
                        time.sleep(0.35)
                        if chegou_ao_fim and passo_scroll >= 4:
                            break

                    novas_nesta_pagina = len(mapa_rotas_capturadas) - qtd_antes_pagina

                    # Se não veio nenhuma rota nova nesta página ou já atingiu o total, encerra imediatamente!
                    if novas_nesta_pagina == 0:
                        break
                    if (total_esperado > 0 and len(mapa_rotas_capturadas) >= total_esperado) or pagina_atual >= 15:
                        break

                    if ir_para_proxima_pagina_meli(page, pagina_atual + 1, primeiro_id_pagina):
                        pagina_atual += 1
                        continue
                    break
            finally:
                parar_aviso_raspagem.set()

            dados_raspados = list(mapa_rotas_capturadas.values())
                    
            if dados_raspados:
                log_painel(f"📦 {hub_alvo}: {len(dados_raspados)} rota(s) capturada(s)! Atualizando planilha...")
                df_novo = pd.DataFrame(dados_raspados)
                try:
                    insp = inspect(motor_db)
                    
                    if insp.has_table('rotas_monitoramento'):
                        df_antigo = pd.read_sql_table('rotas_monitoramento', con=motor_db)
                        for col_extra in ['monitor', 'status_manual', 'restante', 'ocorrencia', 'revertido', 'observacoes', 'cores_customizadas']:
                            if col_extra not in df_antigo.columns:
                                df_antigo[col_extra] = ""
                        
                        df_antigo['id_rota'] = df_antigo['id_rota'].astype(str)
                        df_novo['id_rota'] = df_novo['id_rota'].astype(str)
                        
                        df_antigo = df_antigo.drop_duplicates(subset=['id_rota'], keep='last')
                        df_novo = df_novo.drop_duplicates(subset=['id_rota'], keep='last')
                        
                        data_hoje = pd.Timestamp.now().strftime('%d/%m/%Y')
                        df_antigo_passado = df_antigo[df_antigo['data'] != data_hoje].copy()
                        df_antigo_hoje = df_antigo[df_antigo['data'] == data_hoje].copy()
                        
                        mapa_monitores = {}
                        for _, row_ant in df_antigo_hoje.iterrows():
                            mon_val = str(row_ant.get('monitor', '') or '').upper().strip()
                            clus_val = str(row_ant.get('cluster', '') or '').upper().strip()
                            hub_val = str(row_ant.get('nome_rota', '') or '').split('_')[0].upper().strip()
                            if mon_val and clus_val and clus_val != "SEM CLUSTER":
                                mapa_monitores[(hub_val, clus_val)] = mon_val

                        df_antigo_hoje.set_index('id_rota', inplace=True)
                        df_novo.set_index('id_rota', inplace=True)
                        
                        indices_comuns = df_antigo_hoje.index.intersection(df_novo.index)
                        for idx in indices_comuns:
                            h_antigo = str(df_antigo_hoje.at[idx, 'horario']).upper().strip()
                            h_novo = str(df_novo.at[idx, 'horario']).upper().strip()
                            is_antigo_bom = (h_antigo not in ["SEM HORÁRIO", "NAN", ""] and "ATA" not in h_antigo and "ETA" not in h_antigo)
                            is_novo_ruim = ("ATA" in h_novo or "ETA" in h_novo or h_novo in ["SEM HORÁRIO", ""])
                            if is_antigo_bom and is_novo_ruim:
                                df_novo.at[idx, 'horario'] = df_antigo_hoje.at[idx, 'horario']

                            c_antigo = str(df_antigo_hoje.at[idx, 'cluster']).strip()
                            c_novo = str(df_novo.at[idx, 'cluster']).strip()
                            if c_antigo and c_antigo.upper() != "SEM CLUSTER" and c_novo.upper() == "SEM CLUSTER":
                                df_novo.at[idx, 'cluster'] = c_antigo

                            p_antiga = str(df_antigo_hoje.at[idx, 'placa']).strip()
                            p_nova = str(df_novo.at[idx, 'placa']).strip()
                            if p_antiga and p_antiga.upper() != "NAN" and not p_nova:
                                df_novo.at[idx, 'placa'] = p_antiga

                            st_antigo = str(df_antigo_hoje.at[idx, 'status'] or '').upper().strip()
                            foi_manual = str(df_antigo_hoje.at[idx, 'status_manual'] or '').upper().strip() == "SIM"
                            status_exclusivo_manual = st_antigo in ["DECLÍNIO", "DECLINIO"]

                            # Preserva o status antigo apenas se foi alterado manualmente pelo time (ou Declínio)
                            if foi_manual or status_exclusivo_manual:
                                df_novo.at[idx, 'status'] = st_antigo

                        colunas_atualizar = ['nome_rota', 'cluster', 'status', 'horario', 'motorista', 'placa', 'estimado_total', 'coletado_geral', 'restante']
                        col_presentes = [col for col in colunas_atualizar if col in df_antigo_hoje.columns and col in df_novo.columns]
                        
                        df_antigo_hoje.update(df_novo[col_presentes])
                        
                        rotas_novas = df_novo[~df_novo.index.isin(df_antigo_hoje.index)].copy()
                        
                        if not rotas_novas.empty:
                            monitores_novas = []
                            for _, r_nova in rotas_novas.iterrows():
                                h_n = str(r_nova.get('nome_rota', '') or '').split('_')[0].upper().strip()
                                c_n = str(r_nova.get('cluster', '') or '').upper().strip()
                                monitores_novas.append(mapa_monitores.get((h_n, c_n), ""))
                            rotas_novas['monitor'] = monitores_novas
                            rotas_novas['status_manual'] = ""
                            rotas_novas['ocorrencia'] = ""

                        df_antigo_hoje.reset_index(inplace=True)
                        rotas_novas.reset_index(inplace=True)
                        
                        df_final = pd.concat([df_antigo_passado, df_antigo_hoje, rotas_novas], ignore_index=True)
                        df_final.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
                    else:
                        df_novo['monitor'] = ""
                        df_novo['status_manual'] = ""
                        df_novo['ocorrencia'] = ""
                        df_novo.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
                        
                except Exception as e:
                    log_painel(f"⚠️ Erro ao cruzar dados antigos: {e}")
                
                return True
            else:
                if tentativa == 0:
                    log_painel(f"⏳ {hub_alvo}: 0 rotas lidas na 1ª verificação. Realizando confirmação de segurança...")
                    continue
                log_painel(f"ℹ️ {hub_alvo}: Nenhuma rota encontrada na tela após confirmação (0 rotas).")
                return True

        except Exception as e:
            detalhe_erro = str(e).splitlines()[0] if str(e) else "Erro desconhecido"
            log_painel(f"⚠️ Erro na tentativa {tentativa+1} ({hub_alvo}): {detalhe_erro}")
            time.sleep(3)
            
    return False

# ==========================================
# MÓDULO 2: DOWNLOAD DE RELATÓRIO (COM ESPERA DE CARREGAMENTO ATÉ 30S)
# ==========================================
def executar_relatorio(page, lista_hubs, ticket):
    for tentativa in range(2):
        try:
            log_painel(f"📥 [RELATÓRIO] Extraindo dados para: {', '.join(lista_hubs)}")
            if not os.path.exists(PASTA_TEMP):
                os.makedirs(PASTA_TEMP, exist_ok=True)
                
            page.goto("https://envios.adminml.com/logistics/reports/first-mile/reports-collections/query", wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)

            if pagina_esta_no_okta(page):
                log_painel("🔐 Sessão pediu reautenticação ao abrir relatório. Verificando login...")
                garantir_login(page)
                page.goto("https://envios.adminml.com/logistics/reports/first-mile/reports-collections/query", wait_until="domcontentloaded", timeout=60000)
                time.sleep(5)
            
            regras_str = ticket.get("regras", "{}")
            try:
                regras = json.loads(regras_str) if isinstance(regras_str, str) else regras_str
            except Exception:
                regras = {}
            
            exportar_local = regras.get("exportar_local", "sistema")
            
            log_painel("📅 Selecionando filtro de data: Hoje")
            try:
                campo_data = page.locator('#input-date-rangeweek').first
                campo_data.wait_for(state="visible", timeout=15000)
                campo_data.click()
                time.sleep(1.2)
                
                btn_hoje = page.locator('td[data-today="true"] button').first
                if btn_hoje.count() > 0 and btn_hoje.is_visible():
                    btn_hoje.click()
                    time.sleep(0.5)
                else:
                    hoje_str = datetime.datetime.now().strftime('%Y-%m-%d')
                    btn_fallback = page.locator(f'td[data-day="{hoje_str}"] button').first
                    if btn_fallback.count() > 0 and btn_fallback.is_visible():
                        btn_fallback.click()
                    time.sleep(0.5)
                    
                btn_aplicar_data = page.locator('span[data-andes-button-content="true"]:has-text("Aplicar")').first
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
                        log_painel(f"⚠️ HUB {h} ignorado (Não encontrado na lista de depósitos).")
                        
                if marcados == 0:
                    log_painel("❌ Erro: Nenhum HUB válido para relatório.")
                    return False
            else:
                log_painel("🌎 Extraindo relatório Global (Sem filtro de depósito).")
                
            btn_ver = page.locator('span[data-andes-button-content="true"]:has-text("Ver resultados")').first
            if btn_ver.count() > 0 and btn_ver.is_visible():
                btn_ver.click()
                
            log_painel("⏳ Aguardando processamento do relatório no Mercado Livre...")
            botao_baixar = page.locator('button[data-andes-button="true"]:has(span:has-text("Baixar"))').first
            
            # Aguarda até 30 segundos o botão "Baixar" ficar habilitado (evita falso "Não há resultados" em relatórios grandes)
            habilitou_baixar = False
            for _ in range(15):
                time.sleep(2)
                if botao_baixar.count() > 0 and botao_baixar.is_visible() and not botao_baixar.is_disabled():
                    habilitou_baixar = True
                    break
                # Se aparecer mensagem explícita de que não há dados na tela, encerra antes
                if page.locator("text=Não foram encontrados resultados, text=Sem resultados").count() > 0:
                    break
            
            if not habilitou_baixar:
                log_painel(f"📭 Não há resultados de paradas disponíveis para ({', '.join(lista_hubs)}) após aguardar carregamento.")
                return True 
                
            with page.expect_download(timeout=60000) as download_info:
                botao_baixar.click()
                
            caminho_arquivo = os.path.join(PASTA_TEMP, download_info.value.suggested_filename)
            download_info.value.save_as(caminho_arquivo)
            
            try:
                if caminho_arquivo.endswith('.csv'): 
                    try:
                        df = pd.read_csv(caminho_arquivo, encoding='utf-8')
                    except Exception:
                        df = pd.read_csv(caminho_arquivo, sep=None, engine='python', encoding='latin1')
                else: 
                    df = pd.read_excel(caminho_arquivo)
                
                insp = inspect(motor_db)
                df.columns = df.columns.str.strip()

                # Mantém apenas as colunas necessárias para os painéis (reduz consumo de RAM em 70% às 16h!)
                colunas_uteis_rel = [
                    'ID do Rota', 'Nome da Rota', 'Data da rota', 'Data', 'ID do Motorista',
                    'Tipo de parada', 'Parada', 'ID de Parada', 'Horários', 'ONTIME',
                    'Pontualidade', 'Hora de coleta concluída', 'Pacotes coletados',
                    'Pacotes estimados', 'Pacotes preparados', 'Tipo do Rota',
                    'Motivo', 'Detalhes', 'Pacotes não preparados/Não estão na agência',
                    'Pacotes não cabem no veículo', 'Pacotes em mau estado'
                ]
                cols_manter = [c for c in colunas_uteis_rel if c in df.columns]
                if len(cols_manter) >= 5:
                    df = df[cols_manter].copy()

                qtd_linhas = len(df)
                
                if insp.has_table('rotas_brutas'):
                    try:
                        colunas_existentes = [c['name'] for c in insp.get_columns('rotas_brutas')]
                        novas_cols = [c for c in df.columns if c not in colunas_existentes]
                        if novas_cols:
                            with motor_db.begin() as conn:
                                for c in novas_cols:
                                    conn.execute(text(f'ALTER TABLE rotas_brutas ADD COLUMN "{c}" TEXT;'))

                        if 'ID do Rota' in df.columns and 'ID do Rota' in colunas_existentes:
                            ids_novos = [str(x).strip() for x in df['ID do Rota'].dropna().unique().tolist() if str(x).strip()]
                            # Remove apenas as rotas atualizadas direto no PostgreSQL sem carregar a tabela inteira na RAM!
                            with motor_db.begin() as conn:
                                for i in range(0, len(ids_novos), 500):
                                    lote = ids_novos[i:i+500]
                                    params = {f"id_{j}": val for j, val in enumerate(lote)}
                                    placeholders = ", ".join([f":id_{j}" for j in range(len(lote))])
                                    conn.execute(text(f'DELETE FROM rotas_brutas WHERE TRIM(CAST("ID do Rota" AS TEXT)) IN ({placeholders})'), params)
                            
                            df.to_sql('rotas_brutas', con=motor_db, if_exists='append', index=False, chunksize=1000)
                        else:
                            df.to_sql('rotas_brutas', con=motor_db, if_exists='replace', index=False, chunksize=1000)
                            
                        log_painel(f"✅ Relatório sincronizado ({qtd_linhas} linhas processadas).")
                    except Exception as e:
                        log_painel(f"⚠️ Aviso ao mesclar via SQL ({e}). Atualizando tabela...")
                        df.to_sql('rotas_brutas', con=motor_db, if_exists='replace', index=False, chunksize=1000)
                else:
                    df.to_sql('rotas_brutas', con=motor_db, if_exists='replace', index=False, chunksize=1000)
                
                del df
                gc.collect()
            except Exception as e:
                log_painel(f"⚠️ Erro ao processar arquivo no DB: {e}")
                
            if exportar_local == "local":
                try:
                    pasta_export = os.path.join(PASTA_TEMP, "downloads_pc")
                    os.makedirs(pasta_export, exist_ok=True)
                    
                    extensao = ".csv" if caminho_arquivo.endswith('.csv') else ".xlsx"
                    prefixo = "Global" if "TODOS" in lista_hubs else lista_hubs[0]
                    nome_pc = f"Relatorio_{prefixo}_{datetime.datetime.now().strftime('%d%m%Y_%H%M%S')}{extensao}"
                    shutil.copy(caminho_arquivo, os.path.join(pasta_export, nome_pc))
                    log_painel(f"💾 [DOWNLOAD_PC:{nome_pc}] Transferindo relatório para o seu computador...")
                except Exception as e:
                    log_painel(f"❌ Erro ao preparar download para o PC: {e}")
            
            if os.path.exists(caminho_arquivo):
                os.remove(caminho_arquivo)
                
            return True
            
        except Exception as e:
            detalhe = str(e).splitlines()[0] if str(e) else "Erro desconhecido"
            log_painel(f"⚠️ Erro de página no relatório (Tentativa {tentativa+1}): {detalhe}")
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
        "--mute-audio"
    ]

    primeiro_boot = True

    while True:
        try:
            limpar_travas_sessao()
            with sync_playwright() as p:
                modo_str = "HEADLESS (Servidor)" if HEADLESS_MODE else "INTERFACE VISÍVEL (Local)"
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
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    accept_downloads=True
                )
                page = navegador.pages[0] if navegador.pages else navegador.new_page()
                estado_anterior = ""
                tarefas_desde_ultimo_reset = 0
                page.set_default_timeout(30000)
                page.set_default_navigation_timeout(60000)

                garantir_login(page, forcar_recarregamento=True)
                
                while True:
                    try:
                        cmd_standby = ler_comando_okta_api()
                        if cmd_standby.get("reiniciar_login"):
                            log_painel("🔄 Comando de recarregamento recebido! Reiniciando verificação de login...")
                            garantir_login(page, forcar_recarregamento=True)
                            estado_anterior = ""

                        df_tickets = pd.read_sql_table('tickets_robo', con=motor_db)
                        ticket_processado = False
                        
                        ativos = df_tickets[df_tickets['status'] == 'ATIVO'].copy()
                        qtd_ativos = len(ativos)
                        nomes_fila = [f"{r['hub']} ({r['tipo'].upper()})" for _, r in ativos.iterrows()]

                        prontos_para_rodar = []
                        for index, row in ativos.iterrows():
                            t_dict = row.to_dict()
                            if avaliar_se_deve_rodar(t_dict):
                                prioridade = 0 if t_dict.get("tipo") == "avulso" else 1
                                prontos_para_rodar.append((prioridade, index, t_dict))

                        prontos_para_rodar.sort(key=lambda x: (x[0], x[1]))

                        for pos_fila, (_, _, ticket) in enumerate(prontos_para_rodar, start=1):
                            garantir_login(page, forcar_recarregamento=False)
                            
                            hub_string = ticket['hub']
                            acao = ticket.get('acao', 'monitoramento')
                            tipo_ticket = ticket.get('tipo', 'avulso')
                            lista_hubs = [h.strip() for h in hub_string.split(',')]
                            
                            restantes_prontos = len(prontos_para_rodar) - pos_fila
                            log_painel(f"🚨 EXECUTANDO ({pos_fila}º da Fila): {hub_string} [{tipo_ticket.upper()}] | Ação: {acao.upper()}")
                            if restantes_prontos > 0:
                                prox_nomes = ", ".join([f"{ idx_p + 1 }º {tk['hub']} ({tk['acao'].upper()})" for idx_p, (_, _, tk) in enumerate(prontos_para_rodar[pos_fila:], start=pos_fila)])
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
                                        log_painel(f"✅ PROCESSO CONCLUÍDO para: {hub_string} ({tipo_ticket.upper()})")
                                    elif hub_invalido and tipo_ticket == 'avulso':
                                        log_painel(f"🗑️ Removendo ticket avulso inválido ({hub_string}) da fila.")
                                        with motor_db.begin() as conn:
                                            conn.execute(
                                                text("DELETE FROM tickets_robo WHERE hub = :h AND acao = :a AND tipo = 'avulso'"),
                                                {"h": hub_string, "a": acao}
                                            )
                                        ticket_processado = True
                                        break
                                        
                                elif acao == "relatorio":
                                    sucesso_geral = executar_relatorio(page, lista_hubs, ticket)
                                    if sucesso_geral:
                                        log_painel(f"✅ EXTRAÇÃO DE RELATÓRIO CONCLUÍDA para: {hub_string} ({tipo_ticket.upper()})")
                                        
                                if sucesso_geral:
                                    nova_coleta = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    novo_status = "CONCLUIDO" if tipo_ticket == 'avulso' else "ATIVO"
                                    
                                    with motor_db.begin() as conn:
                                        conn.execute(text("""
                                            UPDATE tickets_robo 
                                            SET ultima_coleta = :uc, status = :st 
                                            WHERE hub = :h AND acao = :a AND tipo = :t
                                        """), {"uc": nova_coleta, "st": novo_status, "h": hub_string, "a": acao, "t": tipo_ticket})
                                    
                                    ticket_processado = True
                                    tarefas_desde_ultimo_reset += 1
                                    break 
                                    
                            except Exception as e:
                                log_painel(f"❌ Erro crítico ao processar ticket: {e}")
                                
                        if not ticket_processado:
                            # Recicla o Chromium a cada 6 tarefas concluídas para manter a memória RAM sempre livre!
                            if tarefas_desde_ultimo_reset >= 6:
                                try:
                                    navegador.close()
                                except Exception:
                                    pass
                                gc.collect()
                                break

                            if estado_anterior != "dormindo":
                                nomes_str = ", ".join(nomes_fila) if nomes_fila else "Nenhum"
                                log_painel(f"💤 Esperando. {qtd_ativos} tarefa(s) na fila: [{nomes_str}].")
                                estado_anterior = "dormindo"
                            time.sleep(4)
                            
                    except Exception as e_inner:
                        log_painel(f"⚠️ Oscilação no loop interno: {e_inner}")
                        time.sleep(5)
                        
        except Exception as e_outer:
            log_painel(f"🔄 Reiniciando motor do navegador ({e_outer})...")
            time.sleep(5)

if __name__ == "__main__":
    iniciar_orquestrador()