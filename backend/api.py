import os
import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import json
import datetime
from sqlalchemy import create_engine, text, inspect
from dotenv import load_dotenv

# Garante que vai localizar o .env na mesma pasta que este api.py
caminho_env = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(caminho_env)

app = FastAPI(title="API - Monitoramento Logístico")

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# Puxa a string de conexão segura do .env
string_conexao = os.getenv("DATABASE_URL")
if not string_conexao:
    raise ValueError("ERRO: A variável DATABASE_URL não foi encontrada no ficheiro .env")

motor_db = create_engine(string_conexao)

# ==========================================
# GESTÃO DE USUÁRIOS E LOGIN
# ==========================================
@app.post("/login")
async def fazer_login(request: Request):
    dados = await request.json()
    try:
        query = text("SELECT username, cargo, hubs_atribuidos FROM usuarios WHERE username = :u AND password = :p")
        with motor_db.connect() as conn:
            resultado = conn.execute(query, {"u": dados.get("username"), "p": dados.get("password")}).fetchone()
            
            if resultado:
                return {
                    "sucesso": True, 
                    "usuario": {
                        "username": resultado[0], 
                        "cargo": resultado[1], 
                        "hubs": resultado[2]
                    }
                }
            return {"sucesso": False, "erro": "Usuário ou senha incorretos."}
    except Exception as e: 
        return {"sucesso": False, "erro": f"Erro no banco: {str(e)}"}

@app.get("/usuarios")
def listar_usuarios():
    try:
        with motor_db.connect() as conn:
            result = conn.execute(text("SELECT username, cargo, hubs_atribuidos FROM usuarios")).fetchall()
            return [{"username": r[0], "cargo": r[1], "hubs": r[2]} for r in result]
    except Exception: 
        return []

@app.post("/add_usuario")
async def add_usuario(request: Request):
    dados = await request.json()
    u = dados.get("username")
    p = dados.get("password")
    c = dados.get("cargo")
    h = dados.get("hubs", "TODOS")
    
    try:
        with motor_db.begin() as conn:
            count = conn.execute(text("SELECT count(*) FROM usuarios WHERE username=:u"), {"u": u}).scalar()
            if count > 0:
                if p: 
                    conn.execute(text("UPDATE usuarios SET password=:p, cargo=:c, hubs_atribuidos=:h WHERE username=:u"), {"u": u, "p": p, "c": c, "h": h})
                else: 
                    conn.execute(text("UPDATE usuarios SET cargo=:c, hubs_atribuidos=:h WHERE username=:u"), {"u": u, "c": c, "h": h})
            else:
                conn.execute(text("INSERT INTO usuarios (username, password, cargo, hubs_atribuidos) VALUES (:u, :p, :c, :h)"), {"u": u, "p": p, "c": c, "h": h})
        return {"sucesso": True}
    except Exception as e: 
        return {"sucesso": False, "erro": str(e)}

@app.delete("/delete_usuario/{username}")
def del_usuario(username: str):
    try:
        with motor_db.begin() as conn: 
            conn.execute(text("DELETE FROM usuarios WHERE username=:u"), {"u": username})
        return {"sucesso": True}
    except Exception as e: 
        return {"sucesso": False, "erro": str(e)}

# ==========================================
# ROTAS DE METAS POR HUB E MÊS
# ==========================================
@app.get("/metas")
def listar_metas():
    try:
        insp = inspect(motor_db)
        if not insp.has_table('metas_hubs'):
            return {}
        df = pd.read_sql_table('metas_hubs', con=motor_db)
        return dict(zip(df['hub'], df['meta']))
    except Exception:
        return {}

@app.post("/salvar_meta")
async def salvar_meta(request: Request):
    dados = await request.json()
    hub = str(dados.get("hub", "TODOS")).upper().strip()
    meta = float(dados.get("meta", 83.0))
    try:
        with motor_db.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS metas_hubs (hub VARCHAR(100) PRIMARY KEY, meta FLOAT)"))
            count = conn.execute(text("SELECT count(*) FROM metas_hubs WHERE hub=:h"), {"h": hub}).scalar()
            if count > 0:
                conn.execute(text("UPDATE metas_hubs SET meta=:m WHERE hub=:h"), {"m": meta, "h": hub})
            else:
                conn.execute(text("INSERT INTO metas_hubs (hub, meta) VALUES (:h, :m)"), {"h": hub, "m": meta})
        return {"sucesso": True}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

@app.post("/deletar_meta")
async def deletar_meta(request: Request):
    dados = await request.json()
    hub = str(dados.get("hub", "")).upper().strip()
    try:
        with motor_db.begin() as conn:
            conn.execute(text("DELETE FROM metas_hubs WHERE hub=:h"), {"h": hub})
        return {"sucesso": True}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

# ==========================================
# EDIÇÕES DE OCORRÊNCIAS (REVERTIDO E OBSERVAÇÕES)
# ==========================================
@app.get("/ocorrencias_edicoes")
def listar_edicoes_ocorrencias():
    try:
        insp = inspect(motor_db)
        if not insp.has_table('controle_ocorrencias'):
            return {}
        df = pd.read_sql_table('controle_ocorrencias', con=motor_db).fillna("")
        resultado = {}
        for _, r in df.iterrows():
            resultado[str(r['chave'])] = {
                "id_rota": str(r.get('id_rota', '')),
                "revertido": str(r.get('revertido', 'NÃO')),
                "observacao": str(r.get('observacao', ''))
            }
        return resultado
    except Exception:
        return {}

@app.post("/salvar_edicao_ocorrencia")
async def salvar_edicao_ocorrencia(request: Request):
    dados = await request.json()
    chave = str(dados.get("chave", "")).strip()
    id_rota = str(dados.get("id_rota", "")).strip()
    revertido = str(dados.get("revertido", "NÃO")).upper().strip()
    observacao = str(dados.get("observacao", "")).strip()

    if not chave:
        return {"sucesso": False, "erro": "Chave inválida"}

    try:
        with motor_db.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS controle_ocorrencias (
                    chave VARCHAR(255) PRIMARY KEY,
                    id_rota VARCHAR(50),
                    revertido VARCHAR(20),
                    observacao TEXT
                )
            """))
            count = conn.execute(text("SELECT count(*) FROM controle_ocorrencias WHERE chave=:c"), {"c": chave}).scalar()
            if count > 0:
                conn.execute(text("""
                    UPDATE controle_ocorrencias 
                    SET id_rota=:ir, revertido=:rv, observacao=:ob 
                    WHERE chave=:c
                """), {"ir": id_rota, "rv": revertido, "ob": observacao, "c": chave})
            else:
                conn.execute(text("""
                    INSERT INTO controle_ocorrencias (chave, id_rota, revertido, observacao) 
                    VALUES (:c, :ir, :rv, :ob)
                """), {"c": chave, "ir": id_rota, "rv": revertido, "ob": observacao})

        if id_rota:
            try:
                insp = inspect(motor_db)
                if insp.has_table('rotas_monitoramento'):
                    df_rotas = pd.read_sql_table('rotas_monitoramento', con=motor_db)
                    df_rotas['id_rota'] = df_rotas['id_rota'].astype(str)
                    if 'revertido' not in df_rotas.columns:
                        df_rotas['revertido'] = ""
                    if id_rota in df_rotas['id_rota'].values:
                        df_rotas.loc[df_rotas['id_rota'] == id_rota, 'revertido'] = revertido
                        df_rotas.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
            except Exception:
                pass

        return {"sucesso": True}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

# ==========================================
# ROTAS DO SISTEMA DE MONITORAMENTO E BI
# ==========================================
@app.get("/rotas")
def puxar_rotas():
    try:
        try:
            with motor_db.begin() as conn:
                conn.execute(text("UPDATE rotas_monitoramento SET nome_rota = 'AVULSA_ANTIGA' WHERE UPPER(nome_rota) LIKE '%NÃO PLANEJADA%' OR UPPER(nome_rota) LIKE '%NAO PLANEJADA%'"))
        except Exception: 
            pass
        
        df = pd.read_sql_table('rotas_monitoramento', con=motor_db).fillna("")
        if 'monitor' in df.columns:
            df['monitor'] = df['monitor'].astype(str).str.upper().str.strip()

        alterou_banco = False
        for idx, row in df.iterrows():
            st_atual = str(row.get('status', '')).strip().upper()
            try:
                est_num = int(float(re.sub(r'[^\d.-]', '', str(row.get('estimado_total', '0'))) or 0))
            except Exception:
                est_num = 0
            try:
                col_num = int(float(re.sub(r'[^\d.-]', '', str(row.get('coletado_geral', '0'))) or 0))
            except Exception:
                col_num = 0

            df.at[idx, 'restante'] = str(est_num - col_num)

            novo_st = st_atual
            if "SEM INÍCIO" in st_atual or "SEM INICIO" in st_atual:
                novo_st = "PENDENTE DE LOGIN"
            elif "ANDAMENTO" in st_atual or st_atual in ["INÍCIO", "INICIO"]:
                novo_st = "LOGADO"
            elif "CONCLU" in st_atual:
                novo_st = "FINALIZADA" if col_num > 0 else "CANCELADA"
            elif st_atual == "FINALIZADO":
                novo_st = "FINALIZADA"

            if novo_st != st_atual:
                df.at[idx, 'status'] = novo_st
                alterou_banco = True

        if alterou_banco:
            try:
                df.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
            except Exception:
                pass

        return df.to_dict(orient="records")
    except Exception as e: 
        return {"erro": str(e)}

@app.get("/rotas_brutas")
def puxar_rotas_brutas():
    try:
        df = pd.read_sql_table('rotas_brutas', con=motor_db)
        return df.fillna("").to_dict(orient="records")
    except Exception: 
        return []

@app.post("/salvar_escala")
async def salvar_escala(request: Request):
    try:
        dados = await request.json()
        data_alvo = str(dados.get('data', '')).strip()
        hub = str(dados.get('hub', 'TODOS')).upper().strip()
        atribuicoes = dados.get('atribuicoes', None)

        df = pd.read_sql_table('rotas_monitoramento', con=motor_db)
        
        if 'monitor' not in df.columns:
            df['monitor'] = ""
        else:
            df['monitor'] = df['monitor'].fillna("").astype(str).str.upper().str.strip()

        if isinstance(atribuicoes, dict):
            for cluster, monitor in atribuicoes.items():
                mon_upper = str(monitor or "").upper().strip()
                condicao = (df['cluster'].astype(str) == str(cluster)) & (df['data'].astype(str).str.contains(data_alvo, na=False))
                if hub and hub != "TODOS":
                    condicao = condicao & (df['nome_rota'].astype(str).str.upper().str.startswith(hub))
                df.loc[condicao, 'monitor'] = mon_upper
        else:
            cluster = str(dados.get('cluster', ''))
            mon_upper = str(dados.get('monitor', '')).upper().strip()
            condicao = (df['cluster'].astype(str) == cluster) & (df['data'].astype(str).str.contains(data_alvo, na=False))
            df.loc[condicao, 'monitor'] = mon_upper

        df.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
        return {"sucesso": True}
    except Exception as e: 
        return {"sucesso": False, "erro": str(e)}

# ==========================================
# ROTAS DO ORQUESTRADOR DO ROBÔ
# ==========================================
@app.get("/tickets")
def listar_tickets():
    try:
        df = pd.read_sql_table('tickets_robo', con=motor_db).fillna("")
        return df[~((df['tipo'] == 'avulso') & (df['status'] == 'CONCLUIDO'))].to_dict(orient="records")
    except Exception: 
        return []

@app.post("/add_ticket")
async def adicionar_ticket(request: Request):
    dados = await request.json()
    hub = dados.get("hub", "TODOS").upper().strip()
    acao = dados.get("acao", "monitoramento")
    if not hub:
        hub = "TODOS"
        
    try:
        try:
            with motor_db.connect() as conn:
                conn.execute(text("SELECT 1 FROM tickets_robo LIMIT 1"))
        except:
            df = pd.DataFrame(columns=["hub", "status", "tipo", "acao", "regras", "ultima_coleta"])
            df.to_sql('tickets_robo', con=motor_db, if_exists='replace', index=False)

        with motor_db.begin() as conn:
            conn.execute(text("DELETE FROM tickets_robo WHERE hub = :h AND acao = :a"), {"h": hub, "a": acao})
            conn.execute(text("""
                INSERT INTO tickets_robo (hub, status, tipo, acao, regras, ultima_coleta) 
                VALUES (:h, 'ATIVO', :t, :a, :r, 'Aguardando')
            """), {
                "h": hub, 
                "t": dados.get("tipo", "avulso"), 
                "a": acao, 
                "r": json.dumps(dados.get("regras", {}))
            })
        return {"sucesso": True, "mensagem": f"HUB {hub} salvo!"}
    except Exception as e: 
        return {"erro": str(e)}

@app.delete("/delete_ticket/{hub}/{acao}")
def deletar_ticket(hub: str, acao: str):
    try:
        with motor_db.begin() as conn:
            conn.execute(text("DELETE FROM tickets_robo WHERE hub = :h AND acao = :a"), {"h": hub, "a": acao})
        return {"sucesso": True}
    except Exception: 
        return {}

# ==========================================
# TELEMETRIA, LOGS E CONTROLO OKTA DO ROBÔ
# ==========================================
logs_memoria = []
total_logs_recebidos = 0
ultimo_heartbeat = datetime.datetime.now()

codigo_okta_pendente = ""
forcar_reinicio_login = False
status_login_robo = "DESCONHECIDO"

def registrar_log_interno(msg: str):
    global total_logs_recebidos
    hora = datetime.datetime.now().strftime("%H:%M:%S")
    logs_memoria.append({
        "indice": total_logs_recebidos,
        "hora": hora,
        "msg": msg
    })
    total_logs_recebidos += 1
    if len(logs_memoria) > 200:
        logs_memoria.pop(0)

@app.get("/heartbeat")
def ping_robo():
    global ultimo_heartbeat
    ultimo_heartbeat = datetime.datetime.now()
    return {"sucesso": True}

@app.post("/enviar_log")
async def receber_log(request: Request):
    dados = await request.json()
    msg = dados.get("mensagem", "")
    registrar_log_interno(msg)
    return {"sucesso": True}

@app.post("/enviar_codigo_okta")
async def receber_codigo_okta(request: Request):
    global codigo_okta_pendente
    dados = await request.json()
    cod = str(dados.get("codigo", "")).replace(" ", "").strip()
    if not cod:
        return {"sucesso": False, "erro": "Código vazio."}
    codigo_okta_pendente = cod
    registrar_log_interno(f"📲 [PAINEL WEB] Código Okta ({cod}) recebido! Enviando para o robô...")
    return {"sucesso": True}

@app.post("/reiniciar_login_robo")
async def solicitar_reinicio_login():
    global forcar_reinicio_login, codigo_okta_pendente
    forcar_reinicio_login = True
    codigo_okta_pendente = ""
    registrar_log_interno("🔄 [PAINEL WEB] Ordem de recarregar site e reiniciar login Okta enviada ao robô!")
    return {"sucesso": True}

@app.get("/obter_comando_okta")
def obter_comando_okta():
    global codigo_okta_pendente, forcar_reinicio_login
    cod = codigo_okta_pendente
    reiniciar = forcar_reinicio_login
    # Limpa após leitura do robô para não repetir o mesmo comando
    codigo_okta_pendente = ""
    forcar_reinicio_login = False
    return {
        "codigo": cod,
        "reiniciar_login": reiniciar
    }

@app.post("/status_login_robo")
async def atualizar_status_login_robo(request: Request):
    global status_login_robo
    dados = await request.json()
    status_login_robo = str(dados.get("status", "DESCONHECIDO"))
    return {"sucesso": True}

@app.get("/ler_logs")
def ler_logs(ultimo_indice: int = 0):
    global ultimo_heartbeat, total_logs_recebidos, status_login_robo
    
    if ultimo_indice > total_logs_recebidos:
        ultimo_indice = 0
        
    novos_logs = [log for log in logs_memoria if log["indice"] >= ultimo_indice]
    robo_on = (datetime.datetime.now() - ultimo_heartbeat).total_seconds() < 8
    
    return {
        "logs": novos_logs,
        "novo_indice": total_logs_recebidos, 
        "robo_online": robo_on,
        "status_login": status_login_robo if robo_on else "OFFLINE"
    }   

# ==========================================
# ATUALIZAÇÕES MANUAIS DA PLANILHA (GRID)
# ==========================================
@app.post("/atualizar_cluster")
async def atualizar_cluster(request: Request):
    dados = await request.json()
    try:
        df = pd.read_sql_table('rotas_monitoramento', con=motor_db)
        df['id_rota'] = df['id_rota'].astype(str)
        id_alvo = str(dados.get("id_rota"))
        novo_cluster = str(dados.get("novo_cluster", "")).upper().strip()
        
        df.loc[df['id_rota'] == id_alvo, 'cluster'] = novo_cluster
        
        if "monitor" in dados:
            if 'monitor' not in df.columns:
                df['monitor'] = ""
            df.loc[df['id_rota'] == id_alvo, 'monitor'] = str(dados.get("monitor", "")).upper().strip()
            
        df.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
        return {"sucesso": True}
    except Exception as e: 
        return {"sucesso": False, "erro": str(e)}

@app.post("/atualizar_observacao")
async def atualizar_obs(request: Request):
    dados = await request.json()
    try:
        df = pd.read_sql_table('rotas_monitoramento', con=motor_db)
        df['id_rota'] = df['id_rota'].astype(str)
        if 'observacoes' not in df.columns: 
            df['observacoes'] = ""
        df.loc[df['id_rota'] == str(dados.get("id_rota")), 'observacoes'] = str(dados.get("observacao"))
        df.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
        return {"sucesso": True}
    except Exception: 
        return {}

@app.post("/atualizar_revertido")
async def atualizar_rev(request: Request):
    dados = await request.json()
    try:
        df = pd.read_sql_table('rotas_monitoramento', con=motor_db)
        df['id_rota'] = df['id_rota'].astype(str)
        if 'revertido' not in df.columns: 
            df['revertido'] = ""
        df.loc[df['id_rota'] == str(dados.get("id_rota")), 'revertido'] = str(dados.get("revertido"))
        df.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
        return {"sucesso": True}
    except Exception: 
        return {}

@app.post("/atualizar_status")
async def atualizar_stat(request: Request):
    dados = await request.json()
    try:
        df = pd.read_sql_table('rotas_monitoramento', con=motor_db)
        df['id_rota'] = df['id_rota'].astype(str)
        if 'status_manual' not in df.columns:
            df['status_manual'] = ""
        id_alvo = str(dados.get("id_rota"))
        novo_status = str(dados.get("status", "")).upper().strip()
        if novo_status == "FINALIZADO":
            novo_status = "FINALIZADA"
        df.loc[df['id_rota'] == id_alvo, 'status'] = novo_status
        df.loc[df['id_rota'] == id_alvo, 'status_manual'] = "SIM" if novo_status != "" else ""
        df.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
        return {"sucesso": True}
    except Exception: 
        return {}

@app.post("/atualizar_cor")
async def atualizar_cor(request: Request):
    dados = await request.json()
    id_rota = str(dados.get("id_rota"))
    coluna = str(dados.get("coluna"))
    cor = str(dados.get("cor"))
    try:
        df = pd.read_sql_table('rotas_monitoramento', con=motor_db)
        df['id_rota'] = df['id_rota'].astype(str)
        if 'cores_customizadas' not in df.columns: 
            df['cores_customizadas'] = "{}"
        if id_rota in df['id_rota'].values:
            idx = df.index[df['id_rota'] == id_rota].tolist()[0]
            str_cores = df.at[idx, 'cores_customizadas']
            try: 
                dict_cores = json.loads(str_cores) if pd.notna(str_cores) and str_cores != "" else {}
            except: 
                dict_cores = {}
            dict_cores[coluna] = cor
            df.at[idx, 'cores_customizadas'] = json.dumps(dict_cores)
            df.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
        return {"sucesso": True}
    except Exception: 
        return {}