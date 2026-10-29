import pandas as pd

def analisar_relatorio():
    caminho_arquivo = "relatorio_rotas_ontem.xlsx"
    
    print("⏳ Desvendando o verdadeiro formato do relatório...")
    
    try:
        # Tentativa 1: Ler como CSV padrão (separado por vírgula)
        df = pd.read_csv(caminho_arquivo)
    except Exception:
        try:
            # Tentativa 2: Ler como CSV padrão brasileiro (separado por ponto e vírgula)
            df = pd.read_csv(caminho_arquivo, sep=';', encoding='latin1')
        except Exception:
            print("❌ Erro: O arquivo não é um CSV válido. Precisaremos abrir no Bloco de Notas para investigar.")
            return
    
    print(f"✅ Sucesso! O arquivo tem {df.shape[0]} linhas e {df.shape[1]} colunas.")
    print("-" * 40)
    print("📋 NOME DAS COLUNAS ORIGINAIS DO MELI:")
    print("-" * 40)
    
    for coluna in df.columns:
        print(f"-> {coluna}")

if __name__ == "__main__":
    analisar_relatorio()