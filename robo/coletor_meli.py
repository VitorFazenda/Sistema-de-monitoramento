import os
import time
import re
import json
import datetime
import threading
import shutil
import requests
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

load_dotenv(CAMINHO_ENV)

DB_URL = os.getenv("DATABASE_URL")
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
MELI_USER = os.getenv("MELI_USER")
MELI_PASS = os.getenv("MELI_PASS")
OKTA_SECRET = os.getenv("OKTA_SECRET", "").strip()
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "False").lower() in ("true", "1", "t")

if not DB_URL:
    print("❌ Erro Crítico: DATABASE_URL não encontrada. Verifique o seu ficheiro .env!")
    exit()

if not MELI_USER or not MELI_PASS:
    print("❌ Erro Crítico: MELI_USER ou MELI_PASS não encontradas no .env!")
    exit()

motor_db = create_engine(DB_URL)
DIAS_SEMANA = ["Segunda", "Terca", "Quarta", "Quinta", "Sexta", "Sabado", "Domingo"]

def log_painel(mensagem):
    print(mensagem, flush=True)
    try:
        requests.post(f"{API_URL}/enviar_log", json={"mensagem": mensagem}, timeout=3)
    except Exception:
        pass

def informar_status_login(status_str):
    try:
        requests.post(f"{API_URL}/status_login_robo", json={"status": status_str}, timeout=2)
    except Exception:
        pass

def ler_comando_okta_api():
    """Consulta na API se o utilizador enviou um código Okta ou pediu para recarregar o site"""
    try:
        r = requests.get(f"{API_URL}/obter_comando_okta", timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {"codigo": "", "reiniciar_login": False}

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
            requests.get(f"{API_URL}/heartbeat", timeout=2)
        except Exception:
            pass
        time.sleep(3)

# ==========================================
# MÓDULO DE LOGIN AUTOMATIZADO (OKTA ESTÁVEL SEM DUPLA NAVEGAÇÃO)
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

    while pagina_esta_no_okta(page):
        cmd = ler_comando_okta_api()

        if cmd.get("reiniciar_login"):
            log_painel("🔄 Reiniciando página de login do Meli a pedido do painel...")
            aviso_okta_enviado = False
            ultimo_user_preenchido = 0
            ultima_senha_preenchida = 0
            try:
                page.goto("https://envios.adminml.com/logistics/first-mile/monitoring", wait_until="domcontentloaded", timeout=60000)
                time.sleep(4)
            except Exception as e:
                log_painel(f"⚠️ Erro ao recarregar página: {e}")
            continue

        codigo_recebido = str(cmd.get("codigo", "")).strip()

        try:
            # 1. Campo de Utilizador (Identifier)
            campo_user = page.locator('input[name="identifier"]')
            if campo_user.count() > 0 and campo_user.first.is_visible():
                # Só preenche o identifier se não houver campo de senha/código visível na mesma tela
                campos_cred_visiveis = page.locator('input[type="password"]:visible, input[name="credentials.passcode"]:visible, input[name="credentials.totp"]:visible').count()
                if campos_cred_visiveis == 0 and (time.time() - ultimo_user_preenchido) > 8:
                    log_painel(f"👤 Preenchendo utilizador ({MELI_USER}) e avançando...")
                    campo_user.first.fill("")
                    campo_user.first.fill(MELI_USER)
                    clicar_botao_avancar_okta(page)
                    ultimo_user_preenchido = time.time()
                    aviso_okta_enviado = False
                    time.sleep(3.5)
                    continue

            # 2. Se aparecer lista de fatores de autenticação
            btn_fator = page.locator('[data-se="okta_verify-totp"] a, [data-se="google_otp"] a, a:has-text("Inserir um código"), a:has-text("Enter a code")')
            if btn_fator.count() > 0 and btn_fator.first.is_visible():
                log_painel("📲 Selecionando opção de código Okta Verify...")
                btn_fator.first.click()
                time.sleep(2.5)
                continue

            # 3. Campos de Senha ou Código Okta
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
                    
                    # Lê o título/label específico do campo (sem ser enganado pelo rodapé do form)
                    titulo_form = ""
                    try:
                        titulo_el = page.locator('h2.okta-form-title, [data-se="o-form-head"], label[for]').first
                        if titulo_el.count() > 0:
                            titulo_form = (titulo_el.text_content() or "").lower()
                    except Exception:
                        pass

                    # Se o utilizador acabou de enviar um código de 6 dígitos pelo painel, prioriza injetar o código se não for explicitamente um input password puro
                    is_tela_codigo = (
                        "verify" in titulo_form or "código" in titulo_form or "code" in titulo_form or
                        tipo_input == "tel" or auto_attr == "one-time-code"
                    )
                    is_tela_senha = (tipo_input == "password" and not is_tela_codigo)

                    if is_tela_senha and not codigo_recebido:
                        if (time.time() - ultima_senha_preenchida) > 6:
                            log_painel("🔑 Tela de palavra-passe detetada! A injetar senha automaticamente...")
                            input_alvo.fill("")
                            input_alvo.fill(MELI_PASS)
                            clicar_botao_avancar_okta(page)
                            ultima_senha_preenchida = time.time()
                            log_painel("⏳ Palavra-passe enviada! A aguardar redirecionamento...")
                            time.sleep(4)
                            continue
                    else:
                        informar_status_login("AGUARDANDO_OKTA")
                        if not aviso_okta_enviado:
                            log_painel("📲 AGUARDANDO CÓDIGO OKTA: Digite os 6 dígitos na 'Área do Robô' e clique em Ativar!")
                            aviso_okta_enviado = True

                        codigo_para_usar = codigo_recebido or gerar_codigo_totp_automatico()
                        if codigo_para_usar:
                            log_painel(f"🚀 Injetando código Okta ({codigo_para_usar}) e avançando...")
                            input_alvo.fill("")
                            input_alvo.fill(codigo_para_usar)
                            clicar_botao_avancar_okta(page)
                            aviso_okta_enviado = False
                            time.sleep(4)
                            continue

        except Exception:
            pass

        time.sleep(1.5)

    time.sleep(3)
    informar_status_login("LOGADO")
    log_painel("✅ Login concluído com sucesso! Sessão ativa e guardada.")
    return True

def traduzir_status_meli(status_bruto: str, coletados_int: int) -> str:
    """Converte os status do Mercado Livre para o padrão oficial da planilha JM"""
    st = str(status_bruto or "").strip().upper()
    if "SEM INÍCIO" in st or "SEM INICIO" in st or st == "PENDENTE DE LOGIN":
        return "PENDENTE DE LOGIN"
    if "ANDAMENTO" in st or "INÍCIO" in st or "INICIO" in st or st == "LOGADO":
        return "LOGADO"
    if "CONCLU" in st or st in ["FINALIZADA", "FINALIZADO", "CANCELADA"]:
        return "FINALIZADA" if coletados_int > 0 else "CANCELADA"
    return st if st else "PENDENTE DE LOGIN"

# ==========================================
# MÓDULO 1: RASPAGEM DE MONITORAMENTO 
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
    """Clica sempre no botão 'Limpar todos' do Mercado Livre e aguarda a tela estabilizar"""
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
            pass
    return False

def executar_raspagem(page, hub_alvo):
    for tentativa in range(2):
        try:
            log_painel(f"⚡ Iniciando raspagem DOM-Read para {hub_alvo} (Tentativa {tentativa+1}/2)...")
            
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

                # Abre o menu Depósito
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

                # Clica diretamente no item da lista (li[role="option"]) e garante que ficou aria-selected="true"
                if opcao_hub.get_attribute("aria-selected") != "true":
                    opcao_hub.click(timeout=5000)
                    time.sleep(0.8)
                    if opcao_hub.get_attribute("aria-selected") != "true":
                        chk_box = opcao_hub.locator('.andes-checkbox__checkbox, input[type="checkbox"]').first
                        if chk_box.count() > 0:
                            chk_box.click(force=True)
                            time.sleep(0.8)

                # Clica no botão Aplicar e aguarda o Mercado Livre carregar as rotas do HUB
                btn_aplicar = page.locator('.compact-selector__footer button:has-text("Aplicar"), button:has(span[data-andes-button-content="true"]:has-text("Aplicar"))').first
                if btn_aplicar.count() > 0 and btn_aplicar.is_visible() and not btn_aplicar.is_disabled():
                    btn_aplicar.click(timeout=8000)
                    aguardar_carregamento_meli(page, tempo_max=15)
                else:
                    page.keyboard.press("Escape")
                    time.sleep(2)

                # Valida se a tag do HUB realmente apareceu dentro do botão Depósito
                tags_aplicadas = [t.strip().upper() for t in combo_deposito.locator('.andes-tag__label').all_inner_texts()]
                if hub_alvo.upper() not in tags_aplicadas:
                    log_painel(f"⚠️ Filtro {hub_alvo} não fixou na 1ª tentativa (tags atuais: {tags_aplicadas}). Repetindo seleção...")
                    if tentativa == 0:
                        continue
            else:
                log_painel("🌎 Filtros limpos! Raspando TODAS as rotas disponíveis no sistema.")

            # Leitura da div.list > div.monitoring-row com espera estendida de até 20s
            dados_raspados = []
            pagina_atual = 1
            seletor_cards = "div.list div.monitoring-row, div.monitoring-row, div.monitoring-row-higher"
            
            while True:
                try:
                    page.wait_for_selector(seletor_cards, timeout=20000)
                    time.sleep(1.0)
                    linhas_rotas = page.locator("div.list > div.monitoring-row").all()
                    if not linhas_rotas:
                        linhas_rotas = page.locator("div.monitoring-row").all()
                    if not linhas_rotas:
                        linhas_rotas = page.locator("div.monitoring-row-higher").all()

                    for linha in linhas_rotas:
                        texto_card_completo = linha.text_content() or ""

                        nome_el = linha.locator("p.monitoring-row-higher-details__text.bold").first
                        nome_bruto = (nome_el.text_content() or "") if nome_el.count() > 0 else ""
                        nome_rota = nome_bruto.replace("Rota ", "").strip()
                        
                        if "NÃO PLANEJADA" in nome_rota.upper() or "NAO PLANEJADA" in nome_rota.upper():
                            prefixo_hub = hub_alvo if hub_alvo != "TODOS" else "AVULSA"
                            nome_rota = f"{prefixo_hub}_ROTA_AVULSA"

                        id_el = linha.locator(".button-copy__text").first
                        id_bruto = (id_el.text_content() or "") if id_el.count() > 0 else ""
                        id_rota = re.sub(r'[^0-9]', '', id_bruto)
                        if not id_rota:
                            continue

                        mot_el = linha.locator('[data-testid="carrier-or-driver-name"]').first
                        motorista = (mot_el.text_content() or "").strip() if mot_el.count() > 0 else ""

                        placa = ""
                        try:
                            placa_el = linha.locator(".monitoring-row-lower-details.vehicle p").first
                            if placa_el.count() > 0:
                                placa_bruta = placa_el.text_content() or ""
                                placa = placa_bruta.split("|")[0].strip()
                        except Exception:
                            placa = ""

                        horario_el = linha.locator(".monitoring-row-higher-details.eta p, div[data-testid='active-route-eta'] p").first
                        horario = (horario_el.text_content() or "").strip() if horario_el.count() > 0 else "Sem Horário"

                        match_col = re.search(r'(\d[\d\.]*)\s*(?:pacotes\s*)?coletados', texto_card_completo, re.IGNORECASE)
                        coletados = re.sub(r'\D', '', match_col.group(1)) if match_col else "0"

                        match_est = re.search(r'(\d[\d\.]*)\s*(?:pacotes\s*)?estimados', texto_card_completo, re.IGNORECASE)
                        estimados = re.sub(r'\D', '', match_est.group(1)) if match_est else "0"

                        int_est = int(estimados) if estimados.isdigit() else 0
                        int_col = int(coletados) if coletados.isdigit() else 0
                        restante = str(int_est - int_col)

                        status_el = linha.locator(".status-main p.bold, .monitoring-row-higher-details.status p.bold").first
                        status_meli_bruto = (status_el.text_content() or "").strip() if status_el.count() > 0 else "Sem início"
                        status_traduzido = traduzir_status_meli(status_meli_bruto, int_col)

                        match_cluster = re.search(r'_(C\d+)_', nome_rota)
                        cluster = match_cluster.group(1) if match_cluster else "Sem Cluster"
                        
                        dados_raspados.append({
                            "id_rota": id_rota,
                            "nome_rota": nome_rota,
                            "cluster": cluster,
                            "horario": horario,
                            "motorista": motorista,
                            "placa": placa,
                            "status": status_traduzido,
                            "estimado_total": str(int_est),
                            "coletado_geral": str(int_col),
                            "restante": restante,
                            "data": pd.Timestamp.now().strftime('%d/%m/%Y')
                        })
                except Exception:
                    pass
                    
                botao_proximo = page.locator('a[role="link"]:has-text("Próximo"), button:has-text("Próximo"), .andes-pagination__button--next a').first
                if botao_proximo.count() > 0 and botao_proximo.is_visible():
                    parent_li = botao_proximo.locator("..")
                    classes_pai = (parent_li.get_attribute("class") or "").lower()
                    if "disabled" not in classes_pai and not botao_proximo.is_disabled():
                        botao_proximo.click(timeout=8000)
                        aguardar_carregamento_meli(page, tempo_max=10)
                        pagina_atual += 1
                        continue
                break
                    
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
                            status_exclusivo_manual = st_antigo in ["DECLÍNIO", "DECLINIO", "COLETANDO"]

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
                # Se veio 0 rotas na Tentativa 1, faz uma releitura completa na Tentativa 2 para garantir que a página não estava apenas carregando!
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
                
                if insp.has_table('rotas_brutas'):
                    try:
                        df_antigo_bruto = pd.read_sql_table('rotas_brutas', con=motor_db)
                        if 'data_extracao' in df_antigo_bruto.columns:
                            df_antigo_bruto.drop(columns=['data_extracao'], inplace=True)
                            
                        df_antigo_bruto.columns = df_antigo_bruto.columns.str.strip()
                        
                        if 'ID do Rota' in df.columns and 'ID do Rota' in df_antigo_bruto.columns:
                            ids_rotas_novas = set(df['ID do Rota'].astype(str).str.strip())
                            df_antigo_preservado = df_antigo_bruto[~df_antigo_bruto['ID do Rota'].astype(str).str.strip().isin(ids_rotas_novas)]
                            df_combinado = pd.concat([df_antigo_preservado, df], ignore_index=True)
                        else:
                            df_combinado = df
                            
                        df_combinado.to_sql('rotas_brutas', con=motor_db, if_exists='replace', index=False)
                        log_painel(f"✅ Relatório sincronizado com exatidão ({len(df)} linhas processadas sem cortes).")
                    except Exception as e:
                        log_painel(f"⚠️ Aviso: Conflito ao mesclar histórico ({e}). Substituindo tabela pela extração atual.")
                        df.to_sql('rotas_brutas', con=motor_db, if_exists='replace', index=False)
                else:
                    df.to_sql('rotas_brutas', con=motor_db, if_exists='replace', index=False)
                
                df_hist = df.copy()
                df_hist['data_extracao'] = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
                
                if insp.has_table('relatorios_historico'):
                    colunas_banco = [c['name'] for c in insp.get_columns('relatorios_historico')]
                    colunas_novas = [c for c in df_hist.columns if c not in colunas_banco]
                    
                    if colunas_novas:
                        try:
                            with motor_db.begin() as conn:
                                for c in colunas_novas:
                                    conn.execute(text(f'ALTER TABLE relatorios_historico ADD COLUMN "{c}" TEXT;'))
                            log_painel(f"🔄 Estrutura do histórico atualizada com {len(colunas_novas)} nova(s) coluna(s).")
                        except Exception as e_alter:
                            log_painel(f"⚠️ Aviso ao adaptar colunas do histórico: {e_alter}")
                
                df_hist.to_sql('relatorios_historico', con=motor_db, if_exists='append', index=False)
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
# CÉREBRO DO ORQUESTRADOR (COM ORDEM DE FILA E ROTINA + AVULSO)
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

    while True:
        try:
            limpar_travas_sessao()
            with sync_playwright() as p:
                modo_str = "HEADLESS (Servidor)" if HEADLESS_MODE else "INTERFACE VISÍVEL (Local)"
                log_painel(f"🤖 ROBÔ INICIADO | Modo: {modo_str}")
                
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

                        # Filtra todos os tickets que devem rodar agora e ordena: 1º Avulsos, 2º Rotinas
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
                                        log_painel(f"✅ MONITORAMENTO CONCLUÍDO para: {hub_string} ({tipo_ticket.upper()})")
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
                                        log_painel(f"✅ RELATÓRIO BAIXADO para: {hub_string} ({tipo_ticket.upper()})")
                                        
                                if sucesso_geral:
                                    nova_coleta = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    novo_status = "CONCLUIDO" if tipo_ticket == 'avulso' else "ATIVO"
                                    
                                    # Atualiza APENAS o ticket do tipo executado (não cancela a Rotina se rodou um Avulso!)
                                    with motor_db.begin() as conn:
                                        conn.execute(text("""
                                            UPDATE tickets_robo 
                                            SET ultima_coleta = :uc, status = :st 
                                            WHERE hub = :h AND acao = :a AND tipo = :t
                                        """), {"uc": nova_coleta, "st": novo_status, "h": hub_string, "a": acao, "t": tipo_ticket})
                                    
                                    ticket_processado = True
                                    break 
                                    
                            except Exception as e:
                                log_painel(f"❌ Erro crítico ao processar ticket: {e}")
                                
                        if not ticket_processado:
                            if estado_anterior != "dormindo":
                                nomes_str = ", ".join(nomes_fila) if nomes_fila else "Nenhum"
                                log_painel(f"💤 Standby. {qtd_ativos} tarefa(s) na fila: [{nomes_str}].")
                                estado_anterior = "dormindo"
                            time.sleep(4)
                            
                    except Exception as e_inner:
                        log_painel(f"⚠️ Oscilação no loop interno: {e_inner}")
                        time.sleep(5)
                        
        except Exception as e_outer:
            log_painel(f"🔄 Reiniciando motor do navegador após falha ({e_outer})...")
            time.sleep(5)

if __name__ == "__main__":
    iniciar_orquestrador()