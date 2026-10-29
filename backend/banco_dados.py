import os
import pandas as pd
from sqlalchemy import create_engine, inspect, text
import re
from dotenv import load_dotenv

# Garante que vai localizar o .env na mesma pasta que este ficheiro (preparação para o Servidor Oracle)
caminho_env = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(caminho_env)

def alimentar_banco():
    # Puxa a string de conexão segura do .env
    string_conexao = os.getenv("DATABASE_URL")
    
    if not string_conexao:
        print("❌ Erro: DATABASE_URL não encontrada no arquivo .env!")
        return

    motor_db = create_engine(string_conexao)
    inspetor = inspect(motor_db)

    # --- INICIALIZAÇÃO DA ESTRUTURA BASE (USUÁRIOS, TICKETS, METAS) ---
    with motor_db.begin() as conexao:
        # 1. Tabela de Utilizadores
        conexao.execute(text("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password VARCHAR(50) NOT NULL,
                cargo VARCHAR(50) NOT NULL,
                hubs_atribuidos VARCHAR(255)
            )
        """))
        # Cria a sua conta de GERENTE por padrão se ela não existir
        res = conexao.execute(text("SELECT count(*) FROM usuarios WHERE username='vitor'"))
        if res.scalar() == 0:
            conexao.execute(text("INSERT INTO usuarios (username, password, cargo, hubs_atribuidos) VALUES ('vitor', 'admin123', 'GERENTE', 'TODOS')"))
            # Contas de teste para poder validar permissões depois
            conexao.execute(text("INSERT INTO usuarios (username, password, cargo, hubs_atribuidos) VALUES ('analista1', '123', 'ANALISTA', 'BRXSP16')"))
            conexao.execute(text("INSERT INTO usuarios (username, password, cargo, hubs_atribuidos) VALUES ('monitor1', '123', 'MONITOR GRAU 1', 'BRXSP10')"))

        # 2. Tabela de Tickets do Robô
        conexao.execute(text("""
            CREATE TABLE IF NOT EXISTS tickets_robo (
                hub VARCHAR(50),
                status VARCHAR(50),
                tipo VARCHAR(50),
                acao VARCHAR(50),
                regras TEXT,
                ultima_coleta VARCHAR(50)
            )
        """))

        # 3. Tabela de Metas dos HUBs
        conexao.execute(text("""
            CREATE TABLE IF NOT EXISTS metas_hubs (
                hub VARCHAR(50) PRIMARY KEY,
                meta FLOAT
            )
        """))

    print("✅ Tabelas de base (Usuários, Tickets e Metas) verificadas e inicializadas com sucesso.")

    caminho_arquivo = "../logistics_packages_06-09-2026.csv" # Arquivo inicial
    print("⏳ Verificando dados iniciais de rotas...")
    
    try:
        df_meli = pd.read_csv(caminho_arquivo)
    except Exception:
        try: 
            df_meli = pd.read_csv(caminho_arquivo, sep=';', encoding='latin1')
        except Exception: 
            print("⚠️ Ficheiro CSV inicial não encontrado. O sistema iniciará a base de rotas limpa e aguardará o robô fazer a 1ª coleta!")
            return # Sai da função em segurança se não houver CSV

    # Só executa daqui para baixo se o ficheiro existir (ex: no seu ambiente local)
    df_meli.to_sql('rotas_brutas', con=motor_db, if_exists='replace', index=False)

    def calcular_pontualidade(series):
        textos = " ".join(series.dropna().astype(str).str.lower())
        if 'late' in textos: return 'LATE'
        if 'early' in textos: return 'EARLY'
        return 'ON TIME'

    df_meli['Nome da Rota'] = df_meli['Nome da Rota'].fillna('ROTA_SEM_NOME')
    extracao = df_meli['Nome da Rota'].str.extract(r'BRX(SP\d{2})_(C\d{2})')
    df_meli['Cluster_Limpo'] = (extracao[0] + " - " + extracao[1]).fillna('Sem Cluster')

    df_novo = df_meli.groupby('ID do Rota', dropna=False).agg({
        'Data da rota': 'first',
        'Cluster_Limpo': 'first', 
        'Nome da Rota': 'first',
        'Horários': 'first',
        'ID do Motorista': 'first',
        'ID do Veículo': 'first',
        'Pontualidade': calcular_pontualidade
    }).reset_index()

    df_novo.rename(columns={
        'Data da rota': 'data', 'Cluster_Limpo': 'cluster', 'ID do Rota': 'id_rota',
        'Nome da Rota': 'nome_rota', 'Horários': 'horario', 'ID do Motorista': 'motorista',
        'ID do Veículo': 'placa', 'Pontualidade': 'pontualidade'
    }, inplace=True)

    if inspetor.has_table('rotas_monitoramento'):
        print("🔄 Atualizando rotas a partir do CSV...")
        df_antigo = pd.read_sql_table('rotas_monitoramento', con=motor_db)
        df_antigo.set_index('id_rota', inplace=True)
        df_novo.set_index('id_rota', inplace=True)
        
        colunas_para_atualizar = ['cluster', 'nome_rota', 'horario', 'motorista', 'placa', 'pontualidade']
        df_antigo.update(df_novo[colunas_para_atualizar])
        
        rotas_novas = df_novo[~df_novo.index.isin(df_antigo.index)]
        df_final = pd.concat([df_antigo, rotas_novas])
        df_final.reset_index(inplace=True)
    else:
        print("✨ Criando tabela de monitoramento de rotas pela primeira vez...")
        df_final = df_novo.copy()
        for col in ['monitor', 'status', 'estimado_total', 'coletado_geral', 'restante', 'ocorrencia', 'revertido', 'coresIndividuais']:
            df_final[col] = ""

    df_final.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
    print("🎉 SUCESSO! Banco de dados estruturado e pronto a usar.")

if __name__ == "__main__":
    alimentar_banco()