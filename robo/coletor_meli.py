# ==========================================
# MÓDULO 1: RASPAGEM DE MONITORAMENTO (NOVO LAYOUT MELI ANDES UI)
# ==========================================
def executar_raspagem(page, hub_alvo):
    for tentativa in range(2):
        try:
            log_painel(f"⚡ Iniciando raspagem DOM-Read para {hub_alvo} (Tentativa {tentativa+1}/2)...")
            page.goto(
                "https://envios.adminml.com/logistics/first-mile/monitoring",
                wait_until="domcontentloaded",
                timeout=60000
            )
            
            time.sleep(5)
            
            # Se caiu na tela de login no meio do processo, refaz o login
            if "auth-meli.adminml.com" in page.url or "okta.com" in page.url:
                log_painel("🔐 Sessão pediu reautenticação durante a abertura. Verificando login...")
                garantir_login(page)
                time.sleep(4)

            if hub_alvo != "TODOS":
                # 1. Localiza o botão do filtro de Depósito (Inspeção 1)
                combo_deposito = page.locator('button.compact-selector__trigger[aria-label="Depósito"], button[role="combobox"][aria-label="Depósito"]').first
                combo_deposito.wait_for(state="visible", timeout=15000)

                # Verifica quais tags já estão selecionadas dentro do botão Depósito
                tags_atuais = [t.strip().upper() for t in combo_deposito.locator('.andes-tag__label').all_inner_texts()]
                
                if tags_atuais == [hub_alvo.upper()]:
                    log_painel(f"✅ Filtro do depósito {hub_alvo} já está ativo na tela!")
                else:
                    # Se houver outros filtros sujos, clica em "Limpar todos" primeiro
                    btn_limpar = page.locator("text=Limpar todos").first
                    if btn_limpar.count() > 0 and btn_limpar.is_visible():
                        btn_limpar.click()
                        time.sleep(2.5)

                    # Abre o dropdown de Depósito
                    if combo_deposito.get_attribute("aria-expanded") != "true":
                        combo_deposito.click()
                        time.sleep(1.5)

                    # Se ainda houver algum outro depósito marcado na lista, desmarca primeiro
                    selecionados = page.locator('li[role="option"][aria-selected="true"]').all()
                    for item_sel in selecionados:
                        chave_sel = (item_sel.get_attribute("data-key") or "").strip().upper()
                        if chave_sel and chave_sel != hub_alvo.upper():
                            item_sel.click()
                            time.sleep(0.4)

                    # 2. Campo Buscar dentro do dropdown (Inspeção 2)
                    campo_busca = page.locator('input.andes-form-control__field[data-andes-searchbox-input="true"], input[placeholder="Buscar"]').first
                    campo_busca.wait_for(state="visible", timeout=10000)
                    campo_busca.fill("")
                    campo_busca.fill(hub_alvo)
                    time.sleep(1.5)

                    # 3. Localiza o item na lista pelo data-key ou role="option" (Inspeção 3)
                    opcao_hub = page.locator(f'li[role="option"][data-key="{hub_alvo}"], li[role="option"]:has-text("{hub_alvo}")').first
                    if opcao_hub.count() == 0 or not opcao_hub.is_visible():
                        log_painel(f"❌ ERRO: O depósito '{hub_alvo}' não existe na lista do Mercado Livre! Verifique a digitação.")
                        page.keyboard.press("Escape")
                        return "HUB_INVALIDO"

                    # Só clica no checkbox se ele ainda NÃO estiver marcado (evita desmarcar!)
                    ja_marcado = opcao_hub.get_attribute("aria-selected") == "true"
                    if not ja_marcado:
                        chk_input = opcao_hub.locator('input.andes-checkbox__input, input[type="checkbox"]').first
                        if chk_input.count() > 0:
                            chk_input.click(force=True)
                        else:
                            opcao_hub.click()
                        time.sleep(1)

                    # 4. Clica no botão Aplicar (Inspeção 4) se estiver habilitado
                    btn_aplicar = page.locator('.compact-selector__footer button:has-text("Aplicar"), button:has(span[data-andes-button-content="true"]:has-text("Aplicar"))').first
                    if btn_aplicar.count() > 0 and btn_aplicar.is_visible() and not btn_aplicar.is_disabled():
                        btn_aplicar.click()
                        time.sleep(4)
                    else:
                        # Se já estava aplicado e o botão Aplicar está cinza, apenas fecha o menu
                        page.keyboard.press("Escape")
                        time.sleep(2)
            else:
                btn_limpar = page.locator("text=Limpar todos").first
                if btn_limpar.count() > 0 and btn_limpar.is_visible():
                    btn_limpar.click()
                    time.sleep(3)
                log_painel("🌎 Raspando TODAS as rotas disponíveis no sistema (Sem Filtro de HUB).")

            # 5. Leitura dos Cards de Rotas (Inspeção 5 - div.monitoring-row-higher)
            dados_raspados = []
            pagina_atual = 1
            while True:
                try:
                    page.wait_for_selector("div.monitoring-row-higher", timeout=10000)
                    linhas_rotas = page.locator("div.monitoring-row-higher").all()
                    
                    for linha in linhas_rotas:
                        # Nome da Rota (ex: "Rota BRXSP10_C06_194")
                        nome_el = linha.locator(".main-data p.monitoring-row-higher-details__text.bold, p.monitoring-row-higher-details__text.bold").first
                        nome_bruto = (nome_el.text_content() or "") if nome_el.count() > 0 else ""
                        nome_rota = nome_bruto.replace("Rota ", "").strip()
                        
                        if "NÃO PLANEJADA" in nome_rota.upper() or "NAO PLANEJADA" in nome_rota.upper():
                            prefixo_hub = hub_alvo if hub_alvo != "TODOS" else "AVULSA"
                            nome_rota = f"{prefixo_hub}_ROTA_AVULSA"

                        # ID da Rota (ex: "Rota ID: 463435197" mesmo dentro de visually-hidden)
                        id_el = linha.locator(".button-copy__text").first
                        id_bruto = (id_el.text_content() or "") if id_el.count() > 0 else ""
                        id_rota = re.sub(r'[^0-9]', '', id_bruto)
                        if not id_rota:
                            continue

                        # Motorista
                        mot_el = linha.locator('[data-testid="carrier-or-driver-name"]').first
                        motorista = (mot_el.text_content() or "").strip() if mot_el.count() > 0 else ""

                        # Placa (tenta ler se o card inferior estiver aberto no DOM)
                        placa = ""
                        try:
                            card_pai = linha.locator("..")
                            placa_el = card_pai.locator(".monitoring-row-lower-details.vehicle p").first
                            if placa_el.count() > 0:
                                placa_bruta = placa_el.text_content() or ""
                                placa = placa_bruta.split("|")[0].strip()
                        except Exception:
                            placa = ""

                        # Horário / ETA / ATA (Novo seletor .monitoring-row-higher-details.eta p)
                        horario_el = linha.locator(".monitoring-row-higher-details.eta p").first
                        horario = (horario_el.text_content() or "").strip() if horario_el.count() > 0 else "Sem Horário"

                        # Status (ex: "Concluída", "Em andamento")
                        status_el = linha.locator(".status-main p.bold, .monitoring-row-higher-details.status p.bold").first
                        status = (status_el.text_content() or "").strip() if status_el.count() > 0 else "Desconhecido"

                        # Pacotes Coletados (ex: "215 pacotes coletados")
                        try:
                            col_el = linha.locator(".monitoring-row-higher-details.packages p.bold, text=coletados").first
                            texto_coletados = col_el.text_content() or "0"
                            coletados = re.sub(r'[^0-9]', '', texto_coletados.split('coletados')[0]) or "0"
                        except Exception:
                            coletados = "0"

                        # Pacotes Estimados (ex: "234 estimados")
                        try:
                            est_el = linha.locator(".monitoring-row-higher-details.packages p:has-text('estimados'), text=estimados").first
                            texto_estimados = est_el.text_content() or "0"
                            estimados = re.sub(r'[^0-9]', '', texto_estimados.split('estimados')[0]) or "0"
                        except Exception:
                            estimados = "0"

                        match_cluster = re.search(r'_(C\d+)_', nome_rota)
                        cluster = match_cluster.group(1) if match_cluster else "Sem Cluster"
                        
                        dados_raspados.append({
                            "id_rota": id_rota,
                            "nome_rota": nome_rota,
                            "cluster": cluster,
                            "horario": horario,
                            "motorista": motorista,
                            "placa": placa,
                            "status": status,
                            "estimado_total": estimados,
                            "coletado_geral": coletados, 
                            "data": pd.Timestamp.now().strftime('%d/%m/%Y')
                        })
                except Exception as e_loop:
                    log_painel(f"ℹ️ Fim da leitura da página {pagina_atual} ({len(dados_raspados)} rotas lidas até agora).")
                    
                botao_proximo = page.locator('a[role="link"]:has-text("Próximo"), button:has-text("Próximo"), .andes-pagination__button--next a').first
                if botao_proximo.count() > 0 and botao_proximo.is_visible():
                    parent_li = botao_proximo.locator("..")
                    classes_pai = (parent_li.get_attribute("class") or "").lower()
                    if "disabled" not in classes_pai and not botao_proximo.is_disabled():
                        botao_proximo.click()
                        time.sleep(3)
                        pagina_atual += 1
                        continue
                break
                    
            if dados_raspados:
                log_painel(f"📦 {hub_alvo}: {len(dados_raspados)} rota(s) capturada(s)! Salvando no banco...")
                df_novo = pd.DataFrame(dados_raspados)
                try:
                    insp = inspect(motor_db)
                    
                    if insp.has_table('rotas_monitoramento'):
                        df_antigo = pd.read_sql_table('rotas_monitoramento', con=motor_db)
                        if 'monitor' not in df_antigo.columns:
                            df_antigo['monitor'] = ""
                        
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
                            
                            is_antigo_bom = (h_antigo != "SEM HORÁRIO" and h_antigo != "NAN" and h_antigo != "" and "ATA" not in h_antigo and "ETA" not in h_antigo)
                            is_novo_ruim = ("ATA" in h_novo or "ETA" in h_novo or h_novo == "SEM HORÁRIO" or h_novo == "")
                            
                            if is_antigo_bom and is_novo_ruim:
                                df_novo.at[idx, 'horario'] = df_antigo_hoje.at[idx, 'horario']

                            c_antigo = str(df_antigo_hoje.at[idx, 'cluster']).strip()
                            c_novo = str(df_novo.at[idx, 'cluster']).strip()
                            if c_antigo and c_antigo.upper() != "SEM CLUSTER" and c_novo.upper() == "SEM CLUSTER":
                                df_novo.at[idx, 'cluster'] = c_antigo

                            # Preserva a placa antiga caso o card estivesse fechado na raspagem
                            p_antiga = str(df_antigo_hoje.at[idx, 'placa']).strip()
                            p_nova = str(df_novo.at[idx, 'placa']).strip()
                            if p_antiga and p_antiga.upper() != "NAN" and not p_nova:
                                df_novo.at[idx, 'placa'] = p_antiga

                        colunas_atualizar = ['nome_rota', 'cluster', 'status', 'horario', 'motorista', 'placa', 'estimado_total', 'coletado_geral']
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

                        df_antigo_hoje.reset_index(inplace=True)
                        rotas_novas.reset_index(inplace=True)
                        
                        df_final = pd.concat([df_antigo_passado, df_antigo_hoje, rotas_novas], ignore_index=True)
                        df_final.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
                    else:
                        df_novo['monitor'] = ""
                        df_novo.to_sql('rotas_monitoramento', con=motor_db, if_exists='replace', index=False)
                        
                except Exception as e:
                    log_painel(f"⚠️ Erro ao cruzar dados antigos: {e}")
                
                return True
            else:
                log_painel(f"ℹ️ {hub_alvo}: Nenhuma rota encontrada na tela neste momento (0 rotas).")
                return True

        except Exception as e:
            detalhe_erro = str(e).splitlines()[0] if str(e) else "Erro desconhecido"
            log_painel(f"⚠️ Erro na tentativa {tentativa+1} ({hub_alvo}): {detalhe_erro}")
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            time.sleep(3)
            
    return False

# ==========================================
# MÓDULO 2: DOWNLOAD DE RELATÓRIO COM OPÇÕES
# ==========================================
def executar_relatorio(page, lista_hubs, ticket):
    for tentativa in range(2):
        try:
            log_painel(f"📥 [RELATÓRIO] Extraindo dados para: {', '.join(lista_hubs)}")
            if not os.path.exists(PASTA_TEMP):
                os.makedirs(PASTA_TEMP, exist_ok=True)
                
            page.goto("https://envios.adminml.com/logistics/reports/first-mile/reports-collections/query", timeout=60000)
            page.wait_for_load_state("networkidle")
            page.reload() 
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            
            regras_str = ticket.get("regras", "{}")
            try: regras = json.loads(regras_str) if isinstance(regras_str, str) else regras_str
            except: regras = {}
            
            exportar_local = regras.get("exportar_local", "sistema")
            
            log_painel("📅 Selecionando filtro de data: Hoje")
            try:
                page.locator('#input-date-rangeweek').first.click()
                time.sleep(1)
                
                btn_hoje = page.locator('td[data-today="true"] button').first
                if btn_hoje.is_visible():
                    btn_hoje.click()
                    time.sleep(0.5)
                else:
                    hoje_str = datetime.datetime.now().strftime('%Y-%m-%d')
                    btn_fallback = page.locator(f'td[data-day="{hoje_str}"] button').first
                    if btn_fallback.is_visible():
                        btn_fallback.click()
                    time.sleep(0.5)
                    
                btn_aplicar_data = page.locator('span[data-andes-button-content="true"]:has-text("Aplicar")').first
                if btn_aplicar_data.is_visible():
                    btn_aplicar_data.click()
                time.sleep(1)
            except Exception as e:
                log_painel(f"⚠️ Aviso: Não foi possível selecionar a data de hoje. Tentando avançar. Detalhe: {e}")
            
            if "TODOS" not in lista_hubs:
                marcados = 0
                
                for h in lista_hubs:
                    label_hub = page.locator(f'label.andes-checkbox:has-text("{h}")').first
                    if label_hub.is_visible():
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
            if btn_ver.is_visible():
                btn_ver.click()
                
            page.wait_for_load_state("networkidle")
            time.sleep(4)
            
            log_painel("⏳ Verificando a disponibilidade do relatório...")
            botao_baixar = page.locator('button[data-andes-button="true"]:has(span:has-text("Baixar"))').first
            
            if botao_baixar.is_disabled():
                log_painel(f"📭 Não há resultados de paradas para a data/HUB selecionado ({', '.join(lista_hubs)}). A ignorar extração.")
                return True 
                
            botao_baixar.wait_for(state="visible", timeout=60000)
            
            with page.expect_download() as download_info:
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
                    pasta_export = os.path.abspath(os.path.join(DIRETORIO_RAIZ, "..", "Relatorios_Extraidos"))
                    if not os.path.exists(pasta_export): os.makedirs(pasta_export, exist_ok=True)
                    
                    extensao = ".csv" if caminho_arquivo.endswith('.csv') else ".xlsx"
                    prefixo = "Global" if "TODOS" in lista_hubs else lista_hubs[0]
                    nome_pc = f"Relatorio_{prefixo}_{datetime.datetime.now().strftime('%d%m%Y_%H%M%S')}{extensao}"
                    shutil.copy(caminho_arquivo, os.path.join(pasta_export, nome_pc))
                    log_painel(f"💾 O Relatório foi SALVO na pasta: Relatorios_Extraidos")
                except Exception as e:
                    log_painel(f"❌ Erro ao mover para a pasta local: {e}")
            
            if os.path.exists(caminho_arquivo): os.remove(caminho_arquivo)
                
            return True
            
        except Exception as e:
            log_painel(f"⚠️ Erro de página no relatório (Tentativa {tentativa+1}). Recarregando...")
            try: page.reload() 
            except: pass
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

    with sync_playwright() as p:
        modo_str = "HEADLESS (Servidor)" if HEADLESS_MODE else "INTERFACE VISÍVEL (Local)"
        log_painel(f"🤖 MOTOR V2.0 (NOVO LAYOUT MELI) INICIADO! Modo: {modo_str}")
        
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
        
        garantir_login(page)
        
        while True:
            try:
                cmd_standby = ler_comando_okta_api()
                if cmd_standby.get("reiniciar_login"):
                    log_painel("🔄 Comando de recarregamento recebido! Reiniciando verificação de login...")
                    garantir_login(page, forcar_recarregamento=True)
                    estado_anterior = ""

                df_tickets = pd.read_sql_table('tickets_robo', con=motor_db)
                ticket_processado = False
                
                ativos = df_tickets[df_tickets['status'] == 'ATIVO']
                qtd_ativos = len(ativos)
                nomes_fila = ativos['hub'].tolist()
                
                for index, row in df_tickets.iterrows():
                    ticket = row.to_dict()
                    
                    if avaliar_se_deve_rodar(ticket):
                        garantir_login(page)
                        
                        hub_string = ticket['hub']
                        acao = ticket.get('acao', 'monitoramento')
                        lista_hubs = [h.strip() for h in hub_string.split(',')]
                        
                        log_painel(f"🚨 EXECUTANDO AGORA: {hub_string} | Ação: {acao.upper()}")
                        log_painel(f"📋 Fila atual: {max(0, qtd_ativos - 1)} rotina(s) aguardando na espera.")
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
                                    log_painel(f"✅ MONITORAMENTO CONCLUÍDO para: {hub_string}")
                                elif hub_invalido and ticket['tipo'] == 'avulso':
                                    log_painel(f"🗑️ Removendo ticket avulso inválido ({hub_string}) da fila para não travar o robô.")
                                    with motor_db.begin() as conn:
                                        conn.execute(text("DELETE FROM tickets_robo WHERE hub = :h AND acao = :a"), {"h": hub_string, "a": acao})
                                    ticket_processado = True
                                    break
                                    
                            elif acao == "relatorio":
                                sucesso_geral = executar_relatorio(page, lista_hubs, ticket)
                                if sucesso_geral:
                                    log_painel(f"✅ RELATÓRIO BAIXADO para: {hub_string}")
                                    
                            if sucesso_geral:
                                nova_coleta = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                novo_status = "CONCLUIDO" if ticket['tipo'] == 'avulso' else "ATIVO"
                                
                                with motor_db.begin() as conn:
                                    conn.execute(text("""
                                        UPDATE tickets_robo 
                                        SET ultima_coleta = :uc, status = :st 
                                        WHERE hub = :h AND acao = :a
                                    """), {"uc": nova_coleta, "st": novo_status, "h": hub_string, "a": acao})
                                
                                ticket_processado = True
                                break 
                                
                        except Exception as e:
                            log_painel(f"❌ Erro crítico ao processar ticket: {e}")
                            
                if not ticket_processado:
                    if estado_anterior != "dormindo":
                        nomes_str = ", ".join(nomes_fila) if nomes_fila else "Nenhum"
                        log_painel(f"💤 Standby. {qtd_ativos} rotina(s) ativas: [{nomes_str}].")
                        estado_anterior = "dormindo"
                    time.sleep(4)
                    
            except Exception:
                time.sleep(5)

if __name__ == "__main__":
    iniciar_orquestrador()