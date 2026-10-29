// ==========================================
// CONFIGURAÇÃO DO SERVIDOR (MUDE AQUI PARA PRODUÇÃO)
// ==========================================
// Quando for enviar para o GitHub Pages, altere esta linha para o URL do seu servidor (ex: "https://api.seuservidor.com")
const API_BASE_URL = "https://147.15.93.48.sslip.io";

// ==========================================
// VARIÁVEIS GLOBAIS
// ==========================================
let currentUser = null; let gridApi = null; let rotasGlobais = []; let rotasBrutas = [];
let celulaAtualFocada = null; let colunaOrigemClick = null;
let clusterData = {}; let monitorAssignments = {}; 
let ultimoIndiceLog = 0; let ticketsAtivos = []; let metasHubs = {}; let hubSendoEditado = "";

let undoStack = []; let isUndoRedoAction = false; let ultimaCorEscolhida = null;

// Armazena as edições de Revertido e Observações feitas no Monitor de Ocorrências
let controleOcorrencias = {};

// Estado global do Monitor de Pontualidade (BI)
let BI_DATA = { raw: [], firstEarly: [], drivers: [], routes: [], clusters: [], routePkgs: {}, pkgsEarly: 0, pkgsLate: 0, pkgsOntime: 0, pkgsTotal: 0 };
let biSortState = {};

// Estado global do Monitor de Ocorrências (OC)
let OC_DATA = { raw: [], rawWithEmpty: [], pivot: [], pivotWithEmpty: [], details: [], totalParadas: 0, totalOcorrencias: 0, pacotesLeftBehind: 0, topMotive: "" };
let filtroMotivoAtualOC = "";

// Estado global do Geral da Operação (GO)
let GO_DATA = {
    totalColetados: 0,
    totalEstimados: 0,
    totalOfensoresPcts: 0,
    totalRevertidosPcts: 0,
    qtdRotasRevertidas: 0,
    qtdRotasComOcorrencia: 0,
    motivosAgrupados: []
};

function getTextColorForBackground(hexColor) {
    if (!hexColor || !hexColor.startsWith('#')) return 'black';
    let r = parseInt(hexColor.substr(1, 2), 16), g = parseInt(hexColor.substr(3, 2), 16), b = parseInt(hexColor.substr(5, 2), 16);
    return (((r * 299) + (g * 587) + (b * 114)) / 1000 >= 128) ? 'black' : 'white';
}

function obterHubFiltroEstatisticas() {
    const el = document.getElementById('filtro-hub-est');
    return el ? el.value : "TODOS";
}

function gerarChaveOcorrencia(routeId, stop, motivo, idxLinha) {
    const r = String(routeId || '').trim();
    const s = String(stop || '').trim();
    const m = String(motivo || '').trim();
    return `${r}||${s}||${m}||${idxLinha}`;
}

function gerarChaveRotaParadaMotivo(routeId, stop, motivo) {
    const r = String(routeId || '').trim();
    const s = String(stop || '').trim();
    const m = String(motivo || '').trim();
    return `${r}||${s}||${m}`;
}

// Extrai o Ano-Mês (YYYY-MM) de uma string de data (DD-MM-YYYY, DD/MM/YYYY ou YYYY-MM-DD)
function extrairAnoMesDeString(strData) {
    if (!strData) return "";
    const limpa = String(strData).trim().split(' ')[0];
    const partes = limpa.split(/[-/]/);
    if (partes.length >= 3) {
        if (partes[0].length === 4) return `${partes[0]}-${String(partes[1]).padStart(2, '0')}`;
        if (partes[2].length === 4) return `${partes[2]}-${String(partes[1]).padStart(2, '0')}`;
    }
    return "";
}

// Identifica qual é o mês/ano ativo no filtro superior de Estatísticas
function obterAnoMesAtivoFiltro() {
    const dSel = document.getElementById('filtro-data-est') ? document.getElementById('filtro-data-est').value : "TODAS";
    if (dSel && dSel !== "TODAS" && dSel !== "PERIODO") {
        const ym = extrairAnoMesDeString(dSel);
        if (ym) return ym;
    }
    if (dSel === "PERIODO") {
        const dIni = document.getElementById('data-ini-est') ? document.getElementById('data-ini-est').value : "";
        const dFim = document.getElementById('data-fim-est') ? document.getElementById('data-fim-est').value : "";
        if (dFim) return dFim.substring(0, 7);
        if (dIni) return dIni.substring(0, 7);
    }
    if (BI_DATA.raw && BI_DATA.raw.length > 0) {
        const ym = extrairAnoMesDeString(BI_DATA.raw[0].date);
        if (ym) return ym;
    }
    const hoje = new Date();
    return `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, '0')}`;
}

// Obtém a Meta On-Time (%) respeitando a hierarquia:
// 1º HUB + Mês | 2º HUB Padrão | 3º TODOS + Mês | 4º TODOS Padrão | 5º 83.0%
function obterMetaAtiva(hubAlvo) {
    const h = String(hubAlvo || obterHubFiltroEstatisticas() || "TODOS").toUpperCase().trim();
    const ym = obterAnoMesAtivoFiltro();

    if (ym && metasHubs[`${h}|${ym}`] !== undefined) return parseFloat(metasHubs[`${h}|${ym}`]);
    if (metasHubs[h] !== undefined) return parseFloat(metasHubs[h]);
    if (ym && metasHubs[`TODOS|${ym}`] !== undefined) return parseFloat(metasHubs[`TODOS|${ym}`]);
    if (metasHubs["TODOS"] !== undefined) return parseFloat(metasHubs["TODOS"]);
    return 83.0;
}

// ==========================================
// CÓPIA INTELIGENTE E ATALHOS GLOBAIS (CTRL+Z / CTRL+D)
// ==========================================
document.addEventListener('copy', function(e) {
    const selection = window.getSelection(); if (selection.isCollapsed) return; 
    const gridDiv = document.getElementById('myGrid');
    if (gridDiv && gridDiv.contains(selection.anchorNode)) {
        const cells = Array.from(gridDiv.querySelectorAll('.ag-cell')).filter(cell => selection.containsNode(cell, true));
        if (cells.length > 0) {
            let rowMap = new Map();
            cells.forEach(c => {
                let row = c.closest('.ag-row');
                if (row) {
                    let rId = row.getAttribute('row-index');
                    if (!rowMap.has(rId)) rowMap.set(rId, []);
                    rowMap.get(rId).push(c.innerText.replace(/\n/g, ' ').trim());
                }
            });
            if (rowMap.size > 0) {
                let sortedKeys = Array.from(rowMap.keys()).sort((a, b) => parseInt(a) - parseInt(b));
                e.clipboardData.setData('text/plain', sortedKeys.map(k => rowMap.get(k).join('\t')).join('\n'));
                e.preventDefault();
            }
        }
    }
});

function desfazerAcao() {
    if (undoStack.length === 0) return;
    let acao = undoStack.pop(); isUndoRedoAction = true; let rowNode = null;
    gridApi.forEachNode(node => { if (String(node.data.id_rota) === String(acao.id_rota)) rowNode = node; });
    if (rowNode) {
        if (acao.type === 'color') {
            if (!rowNode.data.coresIndividuais) rowNode.data.coresIndividuais = {};
            let corRev = acao.oldVal || '#ffffff'; rowNode.data.coresIndividuais[acao.colId] = corRev;
            gridApi.refreshCells({ rowNodes: [rowNode], columns: [acao.colId], force: true });
            fetch(`${API_BASE_URL}/atualizar_cor`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id_rota: acao.id_rota, coluna: acao.colId, cor: corRev }) });
        } else if (acao.type === 'edit') rowNode.setDataValue(acao.colId, acao.oldVal);
    }
    isUndoRedoAction = false;
}

document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.ctrlKey && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); desfazerAcao(); }
    if (e.ctrlKey && (e.key === 'd' || e.key === 'D')) { e.preventDefault(); if (ultimaCorEscolhida) pintarCelulaFocada(ultimaCorEscolhida); }
});

// ==========================================
// SISTEMA DE SEGURANÇA (RBAC) E LOGIN
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    const sessionUser = sessionStorage.getItem('currentUser');
    if(sessionUser) {
        currentUser = JSON.parse(sessionUser);
        document.getElementById('login-overlay').style.display = 'none';
        document.getElementById('nome-usuario-logado').innerHTML = `👤 ${currentUser.username.toUpperCase()}`;
        document.getElementById('menu-nome').innerText = currentUser.username.toUpperCase();
        document.getElementById('menu-cargo').innerText = currentUser.cargo;
        aplicarPermissoesDeCargo(); iniciarSistemaAposLogin();
    }
});

function fazerLogin() {
    const u = document.getElementById('login-user').value, p = document.getElementById('login-pwd').value, err = document.getElementById('login-erro');
    if(!u || !p) { err.innerText = "Preencha utilizador e palavra-passe!"; return; }
    err.innerText = "A conectar...";
    fetch(`${API_BASE_URL}/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({username: u, password: p}) })
    .then(r => r.json()).then(d => {
        if(d.sucesso) {
            currentUser = d.usuario; sessionStorage.setItem('currentUser', JSON.stringify(currentUser));
            document.getElementById('login-overlay').style.display = 'none';
            document.getElementById('nome-usuario-logado').innerHTML = `👤 ${currentUser.username.toUpperCase()}`;
            document.getElementById('menu-nome').innerText = currentUser.username.toUpperCase();
            document.getElementById('menu-cargo').innerText = currentUser.cargo;
            aplicarPermissoesDeCargo(); iniciarSistemaAposLogin();
        } else err.innerText = d.erro;
    }).catch(() => err.innerText = "Erro ao conectar com servidor local.");
}

function fazerLogoff() { sessionStorage.removeItem('currentUser'); location.reload(); }

function podeConfigurarMeta() {
    if (!currentUser) return false;
    return ['GERENTE', 'COORDENADOR', 'ANALISTA'].includes(currentUser.cargo);
}

function aplicarPermissoesDeCargo() {
    const c = currentUser.cargo, h = currentUser.hubs;
    if(document.getElementById('bi-hub-label')) document.getElementById('bi-hub-label').innerText = h;
    if(document.getElementById('oc-hub-label')) document.getElementById('oc-hub-label').innerText = h;
    if(document.getElementById('go-hub-label')) document.getElementById('go-hub-label').innerText = h;
    if(document.getElementById('home-saudacao')) document.getElementById('home-saudacao').innerText = `Bem-vindo(a), ${currentUser.username.toUpperCase()}!`;
    if(document.getElementById('home-cargo-badge')) document.getElementById('home-cargo-badge').innerText = c;
    if(document.getElementById('home-hubs-badge')) document.getElementById('home-hubs-badge').innerText = h;

    const btnMetaNav = document.getElementById('btn-config-meta-nav');
    if (btnMetaNav) {
        btnMetaNav.style.display = podeConfigurarMeta() ? 'flex' : 'none';
    }

    if (c === 'MONITOR GRAU 1' || c === 'MONITOR GRAU 2') {
        if(document.getElementById('opt-raspagem')) document.getElementById('opt-raspagem').style.display = 'none';
        if(document.getElementById('opt-rotina')) document.getElementById('opt-rotina').style.display = 'none';
        if(document.getElementById('config-acao')) document.getElementById('config-acao').value = 'relatorio';
        if(document.getElementById('config-tipo')) document.getElementById('config-tipo').value = 'avulso';
        mudarTipoTicket(); 
        if (h !== "TODOS" && document.getElementById('config-hub')) {
            const inputHub = document.getElementById('config-hub'); inputHub.value = h; inputHub.setAttribute('readonly', true); inputHub.classList.add('trava-seguranca');
        }
        if (c === 'MONITOR GRAU 1') {
            if(document.getElementById('opt-destino-sistema')) document.getElementById('opt-destino-sistema').style.display = 'none';
            if(document.getElementById('config-destino')) document.getElementById('config-destino').value = 'local';
            if(document.getElementById('btn-salvar-escala')) document.getElementById('btn-salvar-escala').disabled = true;
        }
    } else if (c === 'ANALISTA') {
        if (h !== "TODOS" && document.getElementById('config-hub')) {
            const inputHub = document.getElementById('config-hub'); inputHub.value = h; inputHub.setAttribute('readonly', true); inputHub.classList.add('trava-seguranca');
        }
    } else {
        if(document.getElementById('config-hub')){ document.getElementById('config-hub').removeAttribute('readonly'); document.getElementById('config-hub').classList.remove('trava-seguranca'); }
    }
}

function podeEditarLinha(rowData) {
    if (!currentUser || currentUser.cargo === 'MONITOR GRAU 1') return false; 
    if (currentUser.cargo === 'GERENTE' || currentUser.cargo === 'COORDENADOR') return true;
    if (currentUser.hubs !== "TODOS" && rowData && rowData.nome_rota) {
        const tr = rowData.nome_rota.toUpperCase(), mh = currentUser.hubs.toUpperCase().split(',');
        return mh.some(h => tr.includes(h.trim()));
    }
    return true;
}

function isRotaAvulsaOuSemCluster(rowData) {
    if (!rowData) return false;
    const nome = String(rowData.nome_rota || "").toUpperCase(), clus = String(rowData.cluster || "").toUpperCase().trim();
    return nome.includes("AVULSA") || nome.includes("PLANEJADA") || !clus || clus === "SEM CLUSTER";
}

function buscarMonitorDoCluster(clusterAlvo, dataRota, nomeRota) {
    const cLimpo = String(clusterAlvo || "").toUpperCase().trim(); if (!cLimpo || cLimpo === "SEM CLUSTER") return "";
    const hRota = String(nomeRota || "").split('_')[0].toUpperCase().trim();
    for (let r of rotasGlobais) {
        if (String(r.cluster || "").toUpperCase().trim() === cLimpo && String(r.monitor || "").trim() !== "" && r.data === dataRota && (String(r.nome_rota || "").split('_')[0].toUpperCase().trim() === hRota || !hRota)) return String(r.monitor).toUpperCase().trim();
    }
    return monitorAssignments[cLimpo] ? String(monitorAssignments[cLimpo]).toUpperCase().trim() : "";
}

function ordenarRotasPorSequencia(lista) {
    return lista.slice().sort((a, b) => {
        const dtA = String(a.data || ""), dtB = String(b.data || ""); if (dtA !== dtB) return dtB.localeCompare(dtA);
        const cA = String(a.cluster || "").toUpperCase().trim(), cB = String(b.cluster || "").toUpperCase().trim();
        const sA = (!cA || cA === "SEM CLUSTER"), sB = (!cB || cB === "SEM CLUSTER");
        if (sA && !sB) return 1; if (!sA && sB) return -1;
        if (cA !== cB) return cA.localeCompare(cB, undefined, { numeric: true });
        return String(a.id_rota || "").localeCompare(String(b.id_rota || ""), undefined, { numeric: true });
    });
}

// ==========================================
// GESTÃO DE EQUIPA E METAS MENSAIS POR HUB
// ==========================================
function abrirModalEquipe() {
    if(currentUser.cargo !== 'GERENTE' && currentUser.cargo !== 'COORDENADOR') return alert("🔒 Acesso Negado: Apenas o GERENTE e COORDENADOR podem gerir a equipa.");
    fetch(`${API_BASE_URL}/usuarios`).then(r => r.json()).then(dados => {
        document.getElementById('lista-usuarios').innerHTML = dados.map(u => `<div class="user-list-item"><div><b style="color:#1e293b;">${u.username}</b><br><span style="font-size:11px; color:#64748b; font-weight:bold;">${u.cargo} | HUB: ${u.hubs}</span></div><div class="user-list-item-actions"><button class="btn-edit" onclick="editarUsuario('${u.username}', '${u.cargo}', '${u.hubs}')">✏️ Editar</button><button class="btn-del" onclick="excluirUser('${u.username}')">🗑️ Excluir</button></div></div>`).join('');
        document.getElementById('equipeModal').classList.add('show');
    });
}
function editarUsuario(u, c, h) {
    document.getElementById('titulo-form-equipe').innerText = "✏️ Editar Utilizador"; document.getElementById('eq-user').value = u;
    document.getElementById('eq-user').setAttribute('readonly', true); document.getElementById('eq-user').style.backgroundColor = "#e2e8f0";
    document.getElementById('eq-pwd').value = ""; document.getElementById('eq-cargo').value = c; document.getElementById('eq-hubs').value = h;
}
function limparFormularioUsuario() {
    document.getElementById('titulo-form-equipe').innerText = "➕ Novo Utilizador"; document.getElementById('eq-user').value = "";
    document.getElementById('eq-user').removeAttribute('readonly'); document.getElementById('eq-user').style.backgroundColor = "#f8fafc";
    document.getElementById('eq-pwd').value = ""; document.getElementById('eq-cargo').value = "ANALISTA"; document.getElementById('eq-hubs').value = "";
}
function salvarUsuario() {
    const u = document.getElementById('eq-user').value, p = document.getElementById('eq-pwd').value, c = document.getElementById('eq-cargo').value, h = document.getElementById('eq-hubs').value;
    if(!u) return alert("Preencha o nome de utilizador!");
    if(currentUser.cargo === 'COORDENADOR' && (c === 'GERENTE' || c === 'COORDENADOR')) return alert("🔒 Acesso Negado: Coordenadores não podem conceder este nível.");
    fetch(`${API_BASE_URL}/add_usuario`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({username: u, password: p, cargo: c, hubs: h}) }).then(() => { limparFormularioUsuario(); abrirModalEquipe(); });
}
function excluirUser(u) { if(confirm(`Excluir ${u}?`)) fetch(`${API_BASE_URL}/delete_usuario/${u}`, { method: 'DELETE' }).then(() => abrirModalEquipe()); }

function carregarMetas() {
    fetch(`${API_BASE_URL}/metas`).then(r => r.json()).then(d => {
        metasHubs = d || {};
        if(rotasBrutas.length > 0) aplicarFiltroUnificadoEstatisticas();
    }).catch(e => console.error(e));
}

function carregarEdicoesOcorrencias() {
    fetch(`${API_BASE_URL}/ocorrencias_edicoes`).then(r => r.json()).then(d => {
        controleOcorrencias = d || {};
        if(rotasBrutas.length > 0) aplicarFiltroUnificadoEstatisticas();
    }).catch(e => console.error(e));
}

function abrirModalConfigurarMeta(hubPreSelecionado) {
    if (!podeConfigurarMeta()) {
        return alert("🔒 Acesso Negado: Apenas Analistas, Coordenadores e Gerentes podem configurar a meta.");
    }
    const selectHub = document.getElementById('meta-hub-select');
    const hubsDisponiveis = extrairHubs(rotasBrutas.length > 0 ? rotasBrutas : rotasGlobais, rotasBrutas.length > 0 ? 'Nome da Rota' : 'nome_rota');
    
    let opcoesHub = `<option value="TODOS">TODOS (Meta Global)</option>` + hubsDisponiveis.map(h => `<option value="${h}">${h}</option>`).join('');
    if (currentUser && currentUser.hubs && currentUser.hubs !== "TODOS") {
        const meusHubs = currentUser.hubs.split(',').map(x => x.trim().toUpperCase()).filter(Boolean);
        opcoesHub = meusHubs.map(h => `<option value="${h}">${h}</option>`).join('');
    }
    selectHub.innerHTML = opcoesHub;

    const hubInicial = hubPreSelecionado || obterHubFiltroEstatisticas() || "TODOS";
    if (Array.from(selectHub.options).some(o => o.value === hubInicial)) {
        selectHub.value = hubInicial;
    }

    const ymAtivo = obterAnoMesAtivoFiltro();
    const inputMes = document.getElementById('meta-mes-input');
    if (inputMes) inputMes.value = ymAtivo;

    document.getElementById('meta-escopo-mes').value = "MES";
    alternarEscopoMesMeta();
    renderizarListaMetasModal();
    document.getElementById('modalMetaOnTime').classList.add('show');
}

function editarMetaHub(h) {
    abrirModalConfigurarMeta(h);
}

function alternarEscopoMesMeta() {
    const escopo = document.getElementById('meta-escopo-mes').value;
    const boxMes = document.getElementById('box-meta-mes-input');
    if (boxMes) boxMes.style.display = (escopo === 'MES') ? 'block' : 'none';
    atualizarInputValorMetaModal();
}

function atualizarInputValorMetaModal() {
    const h = document.getElementById('meta-hub-select').value;
    const escopo = document.getElementById('meta-escopo-mes').value;
    const ym = document.getElementById('meta-mes-input').value;
    const chave = (escopo === 'MES' && ym) ? `${h}|${ym}` : h;

    let valorAtual = metasHubs[chave];
    if (valorAtual === undefined) valorAtual = obterMetaAtiva(h);
    document.getElementById('input-nova-meta').value = valorAtual;
}

function renderizarListaMetasModal() {
    const cont = document.getElementById('lista-metas-cadastradas');
    if (!cont) return;
    const chaves = Object.keys(metasHubs).sort();
    if (chaves.length === 0) {
        cont.innerHTML = `<div style="color:#64748b; text-align:center; padding:10px;">Nenhuma meta personalizada salva (usando padrão 83.0%).</div>`;
        return;
    }
    cont.innerHTML = chaves.map(k => {
        const [h, ym] = k.includes('|') ? k.split('|') : [k, 'Padrão Geral'];
        const labelData = ym === 'Padrão Geral' ? '🌐 Padrão Geral' : `📅 Mês: ${ym.split('-').reverse().join('/')}`;
        const chaveEscapada = k.replace(/'/g, "\\'");
        return `
            <div onclick="selecionarMetaDaLista('${chaveEscapada}')" style="display:flex; justify-content:space-between; align-items:center; padding:6px 10px; border-bottom:1px solid #e2e8f0; cursor:pointer; border-radius:4px;" onmouseover="this.style.background='#e0f2fe'" onmouseout="this.style.background='transparent'">
                <span><b style="color:#1a237e;">${h}</b> — <span style="color:#475569;">${labelData}</span></span>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span style="background:#dcfce7; color:#15803d; font-weight:800; padding:2px 8px; border-radius:10px;">${Number(metasHubs[k]).toFixed(1)}%</span>
                    <button class="btn-del" style="padding:2px 6px; font-size:10.5px;" onclick="excluirMetaCadastrada('${chaveEscapada}', event)" title="Excluir esta meta e data">🗑️ Excluir</button>
                </div>
            </div>
        `;
    }).join('');
}

function excluirMetaCadastrada(chave, event) {
    if (event) event.stopPropagation();
    if (!podeConfigurarMeta()) return alert("🔒 Sem permissão para excluir metas.");
    const [h, ym] = chave.includes('|') ? chave.split('|') : [chave, 'Padrão Geral'];
    const desc = ym === 'Padrão Geral' ? `${h} (Padrão Geral)` : `${h} (${ym.split('-').reverse().join('/')})`;
    if (!confirm(`Deseja realmente excluir a meta cadastrada de ${desc}?`)) return;

    fetch(`${API_BASE_URL}/deletar_meta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hub: chave })
    }).then(r => r.json()).then(res => {
        if (res.sucesso) {
            delete metasHubs[chave];
            renderizarListaMetasModal();
            atualizarInputValorMetaModal();
            if (rotasBrutas.length > 0) aplicarFiltroUnificadoEstatisticas();
        } else {
            alert("Erro ao excluir meta: " + (res.erro || "Falha no servidor"));
        }
    });
}

function selecionarMetaDaLista(chave) {
    const [h, ym] = chave.includes('|') ? chave.split('|') : [chave, ''];
    const selectHub = document.getElementById('meta-hub-select');
    if (!Array.from(selectHub.options).some(o => o.value === h)) {
        const opt = document.createElement('option');
        opt.value = h; opt.text = h;
        selectHub.appendChild(opt);
    }
    selectHub.value = h;
    if (ym) {
        document.getElementById('meta-escopo-mes').value = 'MES';
        document.getElementById('meta-mes-input').value = ym;
    } else {
        document.getElementById('meta-escopo-mes').value = 'PADRAO';
    }
    alternarEscopoMesMeta();
}

function confirmarEdicaoMeta() {
    if (!podeConfigurarMeta()) return alert("🔒 Sem permissão para editar metas.");
    const h = document.getElementById('meta-hub-select').value;
    const escopo = document.getElementById('meta-escopo-mes').value;
    const ym = document.getElementById('meta-mes-input').value;

    if (escopo === 'MES' && !ym) return alert("Selecione o Mês/Ano de vigência!");
    const chaveFinal = (escopo === 'MES') ? `${h}|${ym}` : h;

    let inp = document.getElementById('input-nova-meta').value;
    if (!inp || inp.trim() === "") return alert("Digite o valor da meta!");
    let val = parseFloat(inp.replace(',', '.'));
    if (isNaN(val) || val <= 0 || val > 100) return alert("Valor inválido! Digite uma percentagem entre 1 e 100.");

    fetch(`${API_BASE_URL}/salvar_meta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hub: chaveFinal, meta: val })
    }).then(r => r.json()).then(res => {
        if(res.sucesso) {
            metasHubs[chaveFinal] = val;
            renderizarListaMetasModal();
            if(rotasBrutas.length > 0) aplicarFiltroUnificadoEstatisticas();
            document.getElementById('modalMetaOnTime').classList.remove('show');
        } else {
            alert("Falha ao salvar meta: " + res.erro);
        }
    });
}

// ==========================================
// GESTÃO DE CORES CUSTOMIZADAS
// ==========================================
function carregarCoresCustomizadas() {
    if (!currentUser) return;
    for (let i = 1; i <= 2; i++) {
        let sc = localStorage.getItem(`customColor_${i}_${currentUser.username}`);
        if (sc) { let sw = document.getElementById(`custom-swatch-${i}`); sw.style.backgroundColor = sc; sw.style.color = getTextColorForBackground(sc); sw.innerText = "C" + i; sw.dataset.color = sc; }
    }
}
function editarCorCustomizada(slot) { document.getElementById(`color-picker-${slot}`).click(); }
function salvarCorCustomizada(slot, cor) {
    if (!currentUser) return; localStorage.setItem(`customColor_${slot}_${currentUser.username}`, cor);
    let sw = document.getElementById(`custom-swatch-${slot}`); sw.style.backgroundColor = cor; sw.style.color = getTextColorForBackground(cor); sw.innerText = "C" + slot; sw.dataset.color = cor;
    document.getElementById("colorMenu").classList.remove("show"); pintarCelulaFocada(cor);
}
function usarCorCustomizada(slot) { let c = document.getElementById(`custom-swatch-${slot}`).dataset.color; if (c) pintarCelulaFocada(c); else editarCorCustomizada(slot); }

// ==========================================
// FILTROS CUSTOMIZADOS AG-GRID
// ==========================================
class ExcelFloatingFilter {
    init(p) {
        this.p = p; this.eGui = document.createElement('div'); this.eGui.style.cssText = 'width:100%; padding:4px 0px; box-sizing:border-box;';
        this.inp = document.createElement('input'); this.inp.type = 'text'; this.inp.style.cssText = 'width:100%; padding:4px; box-sizing:border-box; border:1px solid #ccc; border-radius:4px; font-size:12px; outline:none;';
        this.inp.placeholder = 'Pesquisar...'; this.eGui.appendChild(this.inp);
        this.inp.addEventListener('input', (e) => { this.p.parentFilterInstance(i => { i.setSearchText(e.target.value); }); });
    }
    getGui() { return this.eGui; } onParentModelChanged() { }
}

class ExcelStyleFilter {
    init(p) {
        this.p = p; this.eGui = document.createElement('div'); this.eGui.style.cssText = 'padding:10px; max-height:250px; overflow-y:auto; font-family:"Segoe UI",sans-serif; font-size:13px; color:#333; background:#fff; min-width:180px; box-shadow:0 4px 6px rgba(0,0,0,0.1); border-radius:4px;';
        let bDiv = document.createElement('div'); bDiv.style.cssText = 'margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #eee; display:flex; gap:10px; justify-content:center;';
        bDiv.innerHTML = `<button id="selectAll" style="cursor:pointer; border-radius:4px; padding:4px 8px; font-size:11px; font-weight:bold; border:1px solid #a5d6a7; background:#f8fafc; color:#2e7d32; flex:1;">Marcar</button><button id="deselectAll" style="cursor:pointer; border-radius:4px; padding:4px 8px; font-size:11px; font-weight:bold; border:1px solid #ef9a9a; background:#f8fafc; color:#d32f2f; flex:1;">Limpar</button>`;
        this.eGui.appendChild(bDiv); this.lCont = document.createElement('div'); this.lCont.style.cssText = 'display:flex; flex-direction:column; gap:5px;'; this.eGui.appendChild(this.lCont);
        this.vArr = []; this.sVals = new Set(); this.cbs = []; this.refreshData();
        bDiv.querySelector('#selectAll').addEventListener('click', () => { this.cbs.forEach(i => { i.cb.checked = true; this.sVals.add(i.val); }); this.p.filterChangedCallback(); });
        bDiv.querySelector('#deselectAll').addEventListener('click', () => { this.cbs.forEach(i => { i.cb.checked = false; this.sVals.delete(i.val); }); this.p.filterChangedCallback(); });
        this.onRowDataUpdated = () => this.refreshData(); this.p.api.addEventListener('rowDataUpdated', this.onRowDataUpdated);
    }
    refreshData() {
        let cVals = new Set(); this.p.api.forEachLeafNode(n => { if (n.data) { let v = n.data[this.p.colDef.field]; cVals.add(v == null ? "" : v); } });
        let nArr = Array.from(cVals).sort();
        if (JSON.stringify(nArr) !== JSON.stringify(this.vArr)) { nArr.forEach(v => { if (!this.vArr.includes(v)) this.sVals.add(v); }); this.vArr = nArr; this.renderCheckboxes(); }
    }
    renderCheckboxes() {
        this.lCont.innerHTML = ''; this.cbs = []; let ts = (this.lastSearchText || "").toLowerCase();
        this.vArr.forEach(v => {
            let l = document.createElement('label'); l.style.cssText = 'display:flex; align-items:center; gap:8px; margin-bottom:4px; cursor:pointer;';
            let cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = this.sVals.has(v); cb.value = v;
            cb.addEventListener('change', (e) => { e.target.checked ? this.sVals.add(v) : this.sVals.delete(v); this.p.filterChangedCallback(); });
            this.cbs.push({cb: cb, val: v}); l.appendChild(cb); l.appendChild(document.createTextNode(v === "" ? "(Vazio)" : v));
            if (ts === "" || v.toLowerCase().includes(ts)) this.lCont.appendChild(l);
        });
    }
    setSearchText(t) {
        this.lastSearchText = t.toLowerCase().trim(); this.renderCheckboxes(); this.sVals.clear();
        this.cbs.forEach(i => { if (this.lastSearchText === "" || i.val.toLowerCase().includes(this.lastSearchText)) { i.cb.checked = true; this.sVals.add(i.val); } else i.cb.checked = false; });
        this.p.filterChangedCallback();
    }
    getGui() { return this.eGui; } doesFilterPass(p) { return this.sVals.has(p.data[this.p.colDef.field] || ""); }
    isFilterActive() { return this.sVals.size < this.vArr.length; } getModel() { return this.isFilterActive() ? { values: Array.from(this.sVals) } : null; }
    setModel(m) { if (m && m.values) this.sVals = new Set(m.values); else this.sVals = new Set(this.vArr); this.renderCheckboxes(); }
    destroy() { if(this.onRowDataUpdated) this.p.api.removeEventListener('rowDataUpdated', this.onRowDataUpdated); }
}

class StatusEditor {
    init(p) {
        this.c = document.createElement('div'); this.c.style.cssText = 'width:100%; height:100%; display:flex; align-items:center; background:white;';
        this.i = document.createElement('input'); this.i.type = 'text'; this.i.value = p.value || '';
        this.i.style.cssText = 'width:100%; height:100%; border:none; outline:none; font-weight:bold; text-align:center; font-family:inherit; font-size:13px; color:#1a237e;';
        let dlId = 'dl-status'; this.i.setAttribute('list', dlId);
        if (!document.getElementById(dlId)) { let dl = document.createElement('datalist'); dl.id = dlId; ['CANCELADA', 'DECLÍNIO', 'PENDENTE DE LOGIN', 'LOGADO', 'COLETANDO', 'FINALIZADA'].forEach(o => { let op = document.createElement('option'); op.value = o; dl.appendChild(op); }); document.body.appendChild(dl); }
        this.i.addEventListener('keydown', e => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') e.stopPropagation(); }); this.c.appendChild(this.i);
    }
    getGui() { return this.c; } afterGuiAttached() { this.i.focus(); this.i.select(); }
    getValue() { let v = this.i.value.toUpperCase().trim(); let opt = ['CANCELADA', 'DECLÍNIO', 'PENDENTE DE LOGIN', 'LOGADO', 'COLETANDO', 'FINALIZADA']; return opt.find(o => o === v) || opt.find(o => o.startsWith(v)) || v; }
    isPopup() { return false; }
}

class RevertidoEditor {
    init(p) {
        this.c = document.createElement('div'); this.c.style.cssText = 'width:100%; height:100%; display:flex; align-items:center; background:white;';
        this.i = document.createElement('input'); this.i.type = 'text'; this.i.value = p.value || '';
        this.i.style.cssText = 'width:100%; height:100%; border:none; outline:none; font-weight:bold; text-align:center; font-family:inherit; font-size:13px; color:#1a237e;';
        let dlId = 'dl-revertido'; this.i.setAttribute('list', dlId);
        if (!document.getElementById(dlId)) { let dl = document.createElement('datalist'); dl.id = dlId; ['SIM', 'NÃO'].forEach(o => { let op = document.createElement('option'); op.value = o; dl.appendChild(op); }); document.body.appendChild(dl); }
        this.i.addEventListener('keydown', e => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') e.stopPropagation(); }); this.c.appendChild(this.i);
    }
    getGui() { return this.c; } afterGuiAttached() { this.i.focus(); this.i.select(); }
    getValue() { let v = this.i.value.toUpperCase().trim(); if(v === 'NAO') return 'NÃO'; let opt = ['SIM', 'NÃO']; return opt.find(o => o === v) || opt.find(o => o.startsWith(v)) || v; }
    isPopup() { return false; }
}

// ==========================================
// GRELHA DE ROTAS (SEM OCORRÊNCIA E PONTUALIDADE)
// ==========================================
function pintarCelulaFocada(cor) {
    ultimaCorEscolhida = cor; document.getElementById("colorMenu").classList.remove("show");
    if (!celulaAtualFocada) return; 
    const rN = gridApi.getDisplayedRowAtIndex(celulaAtualFocada.rowIndex), cI = celulaAtualFocada.column.getId();
    if (rN && podeEditarLinha(rN.data)) {
        let oC = (rN.data.coresIndividuais && rN.data.coresIndividuais[cI]) ? rN.data.coresIndividuais[cI] : null;
        if (!isUndoRedoAction && oC !== cor) { undoStack.push({ type: 'color', id_rota: rN.data.id_rota, colId: cI, oldVal: oC, newVal: cor }); if(undoStack.length > 50) undoStack.shift(); }
        if (!rN.data.coresIndividuais) rN.data.coresIndividuais = {}; rN.data.coresIndividuais[cI] = cor;
        gridApi.refreshCells({ rowNodes: [rN], columns: [cI], force: true });
        fetch(`${API_BASE_URL}/atualizar_cor`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id_rota: rN.data.id_rota, coluna: cI, cor: cor }) });
    }
}
function salvarEstadoColunas() { if (gridApi && currentUser) localStorage.setItem('gridState_' + currentUser.username, JSON.stringify(gridApi.getColumnState())); }
function restaurarEstadoColunas() {
    if (!gridApi || !currentUser) return;
    const es = localStorage.getItem('gridState_' + currentUser.username);
    if (es) {
        try {
            const parsed = JSON.parse(es).filter(c => c.colId !== 'ocorrencia' && c.colId !== 'pontualidade');
            gridApi.applyColumnState({ state: parsed, applyOrder: true });
        } catch(e) {
            gridApi.sizeColumnsToFit();
        }
    } else {
        gridApi.sizeColumnsToFit();
    }
}
function atualizarContadorRotas() { if (gridApi && document.getElementById('total-rotas-planilha')) document.getElementById('total-rotas-planilha').innerText = gridApi.getDisplayedRowCount(); }

const columnDefs = [
    { field: "data", headerName: "DATA", width: 120, editable: false, filter: ExcelStyleFilter },
    { field: "monitor", headerName: "MONITOR", width: 130, editable: false, filter: ExcelStyleFilter, floatingFilter: true, floatingFilterComponent: ExcelFloatingFilter },
    { field: "cluster", headerName: "CLUSTER", width: 150, filter: ExcelStyleFilter, floatingFilter: true, floatingFilterComponent: ExcelFloatingFilter, editable: p => podeEditarLinha(p.data) && isRotaAvulsaOuSemCluster(p.data), valueParser: p => { let v = String(p.newValue || "").toUpperCase().trim(); return v === "" ? "Sem Cluster" : v; }, cellStyle: p => { if (isRotaAvulsaOuSemCluster(p.data) && (!p.value || String(p.value).toUpperCase() === 'SEM CLUSTER')) return { backgroundColor: '#fff3cd', color: '#856404', fontWeight: 'bold', border: '1px dashed #ffc107' }; return null; } },
    { field: "id_rota", headerName: "ID DE ROTA", width: 130, editable: false, filter: 'agTextColumnFilter', floatingFilter: true },
    { field: "nome_rota", headerName: "NOME DA ROTA", width: 190, editable: false, filter: 'agTextColumnFilter', floatingFilter: true },
    { field: "horario", headerName: "HORÁRIO", width: 110, editable: false, filter: 'agTextColumnFilter', floatingFilter: true },
    { field: "motorista", headerName: "MOTORISTA", width: 260, editable: false, filter: 'agTextColumnFilter', floatingFilter: true },
    { field: "placa", headerName: "PLACA", width: 110, editable: false, filter: 'agTextColumnFilter', floatingFilter: true },
    { field: "status", headerName: "STATUS", width: 160, editable: p => podeEditarLinha(p.data), cellEditor: StatusEditor, filter: ExcelStyleFilter, cellStyle: p => { const cI = p.column.getId(); if (p.data && p.data.coresIndividuais && p.data.coresIndividuais[cI]) return { backgroundColor: p.data.coresIndividuais[cI], color: getTextColorForBackground(p.data.coresIndividuais[cI]), fontWeight: 'bold' }; let v = p.value ? p.value.toUpperCase() : ''; let b = { fontWeight: 'bold', color: 'white', textAlign: 'center' }; if (v === 'CANCELADA') return { ...b, backgroundColor: '#d32f2f' }; if (v === 'DECLÍNIO') return { ...b, backgroundColor: '#800020' }; if (v === 'PENDENTE DE LOGIN') return { ...b, backgroundColor: '#ffeb3b', color: 'black' }; if (v === 'LOGADO') return { ...b, backgroundColor: '#2e7d32' }; if (v === 'COLETANDO') return { ...b, backgroundColor: '#90ee90', color: 'black' }; if (v === 'FINALIZADA') return { ...b, backgroundColor: '#004d40' }; return null; } },
    { field: "estimado_total", headerName: "ESTIMADO TOTAL", width: 140, editable: p => podeEditarLinha(p.data), filter: 'agTextColumnFilter', floatingFilter: true, valueParser: p => p.newValue ? p.newValue.replace(/\D/g, '') : '' },
    { field: "coletado_geral", headerName: "COLETADOS", width: 120, editable: p => podeEditarLinha(p.data), filter: 'agTextColumnFilter', floatingFilter: true, valueParser: p => p.newValue ? p.newValue.replace(/\D/g, '') : '' },
    { field: "restante", headerName: "RESTANTES", width: 120, editable: false, filter: 'agTextColumnFilter', floatingFilter: true, valueGetter: p => { return (parseFloat(p.data.estimado_total) || 0) - (parseFloat(p.data.coletado_geral) || 0); } },
    { field: "revertido", headerName: "REVERTIDO?", width: 120, editable: p => podeEditarLinha(p.data), cellEditor: RevertidoEditor, filter: ExcelStyleFilter, cellStyle: p => { const cI = p.column.getId(); if (p.data && p.data.coresIndividuais && p.data.coresIndividuais[cI]) return { backgroundColor: p.data.coresIndividuais[cI], color: getTextColorForBackground(p.data.coresIndividuais[cI]), fontWeight: 'bold' }; let v = p.value ? p.value.toUpperCase() : ''; let b = { fontWeight: 'bold', color: 'white', textAlign: 'center' }; if (v === 'SIM') return { ...b, backgroundColor: '#2e7d32' }; if (v === 'NÃO') return { ...b, backgroundColor: '#d32f2f' }; return null; } },
    { field: "observacoes", headerName: "OBSERVAÇÕES DA ROTA", width: 270, editable: p => podeEditarLinha(p.data), filter: 'agTextColumnFilter', floatingFilter: true }
];

const gridOptions = { 
    columnDefs: columnDefs, rowData: [], enableCellChangeFlash: true, enableCellTextSelection: true, ensureDomOrder: true, alwaysMultiSort: true, 
    onModelUpdated: atualizarContadorRotas, onFilterChanged: atualizarContadorRotas, onRowDataUpdated: atualizarContadorRotas,
    onColumnResized: () => salvarEstadoColunas(), onColumnMoved: () => salvarEstadoColunas(), onColumnVisible: () => salvarEstadoColunas(), onColumnPinned: () => salvarEstadoColunas(),
    defaultColDef: { 
        editable: true, sortable: true, resizable: true,
        cellStyle: p => {
            const cI = p.column.getId(); if (p.data && p.data.coresIndividuais && p.data.coresIndividuais[cI]) return { backgroundColor: p.data.coresIndividuais[cI], color: getTextColorForBackground(p.data.coresIndividuais[cI]), fontWeight: 'bold' };
            return null; 
        }
    },
    onCellClicked: p => { colunaOrigemClick = p.column.getId(); }, onCellFocused: p => { celulaAtualFocada = p; },
    onCellKeyDown: p => {
        const e = p.event;
        if (e.ctrlKey && (e.key === 'c' || e.key === 'C')) { const s = window.getSelection(); if (s.isCollapsed && p.value) { navigator.clipboard.writeText(p.value); e.preventDefault(); } }
        if (e.key === 'Enter' && colunaOrigemClick) { let nR = p.node.rowIndex + 1; setTimeout(() => { gridApi.setFocusedCell(nR, colunaOrigemClick); }, 50); }
        if (e.ctrlKey && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); desfazerAcao(); }
        if (e.ctrlKey && (e.key === 'd' || e.key === 'D')) { e.preventDefault(); if (ultimaCorEscolhida) pintarCelulaFocada(ultimaCorEscolhida); }
    },
    onCellValueChanged: p => {
        if (!isUndoRedoAction && p.oldValue !== p.newValue) { undoStack.push({ type: 'edit', id_rota: p.data.id_rota, colId: p.colDef.field, oldVal: p.oldValue, newVal: p.newValue }); if(undoStack.length > 50) undoStack.shift(); }
        let ep = ""; let bL = { id_rota: p.data.id_rota };
        if (p.colDef.field === 'cluster') { 
            ep = "atualizar_cluster"; let cF = String(p.newValue || "").toUpperCase().trim(); if (!cF || cF === "SEM CLUSTER") cF = "Sem Cluster";
            p.data.cluster = cF; bL.novo_cluster = cF; let mA = buscarMonitorDoCluster(cF, p.data.data, p.data.nome_rota); p.data.monitor = mA; bL.monitor = mA;
            rotasGlobais.forEach(r => { if (String(r.id_rota) === String(p.data.id_rota)) { r.cluster = cF; r.monitor = mA; } });
            rotasGlobais = ordenarRotasPorSequencia(rotasGlobais); aplicarFiltroHubPlanilha(); inicializarEscala();
        } else if (p.colDef.field === 'observacoes') { ep = "atualizar_observacao"; bL.observacao = p.newValue; }
        else if (p.colDef.field === 'revertido') {
            ep = "atualizar_revertido"; bL.revertido = p.newValue;
            rotasGlobais.forEach(r => { if (String(r.id_rota) === String(p.data.id_rota)) r.revertido = p.newValue; });
            if (rotasBrutas.length > 0) aplicarFiltroUnificadoEstatisticas();
        }
        else if (p.colDef.field === 'status') { ep = "atualizar_status"; bL.status = p.newValue; }
        if(ep !== "") fetch(`${API_BASE_URL}/${ep}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(bL) }).catch(e => console.error(e));
    }
};

// ==========================================
// FUNÇÕES AUXILIARES E FILTRO UNIFICADO DE ESTATÍSTICAS
// ==========================================
function extrairHubs(dados, cNome) {
    let h = new Set();
    dados.forEach(r => { let n = r[cNome]; if (n) { let p = String(n).split('_'); let px = p[0].toUpperCase().trim(); if (px && !px.includes("NÃO PLANEJADA") && !px.includes("NAO PLANEJADA") && !px.includes("AVULSA")) h.add(px); } });
    return Array.from(h).sort();
}
function extrairDatasBI(dados) { let dSet = new Set(); dados.forEach(r => { let d = r['Data da rota'] || r['Data']; if (d) dSet.add(String(d).trim()); }); return Array.from(dSet).sort().reverse(); }

function populate(id, opts, def) { let el = document.getElementById(id); if(!el) return; let v = el.value; el.innerHTML = opts; if(v && v !== def && Array.from(el.options).some(o => o.value === v)) el.value = v; else el.value = def; }

function popularDropdownsHubMonitoramento(hubs) {
    const o = `<option value="TODOS">Todos os HUBs</option>` + hubs.map(h => `<option value="${h}">${h}</option>`).join('');
    populate('filtro-hub-planilha', o, "TODOS"); populate('filtro-hub-escala', o, "TODOS");
}
function popularDropdownsHubBI(hubs) {
    const o = `<option value="TODOS">Todos os HUBs</option>` + hubs.map(h => `<option value="${h}">${h}</option>`).join('');
    populate('filtro-hub-est', o, "TODOS");
}
function popularDropdownsDataBI(datas) {
    const o = `<option value="TODAS">Todas as Datas</option><option value="PERIODO">📅 Selecionar Período...</option>` + datas.map(d => `<option value="${d}">${d}</option>`).join('');
    populate('filtro-data-est', o, "TODAS");
}

function aplicarFiltroHubPlanilha() {
    if (!gridApi) return; const hS = document.getElementById('filtro-hub-planilha').value;
    let dF = rotasGlobais; if (hS !== "TODOS") dF = rotasGlobais.filter(r => r.nome_rota && r.nome_rota.toUpperCase().startsWith(hS));
    gridApi.setGridOption('rowData', ordenarRotasPorSequencia(dF)); setTimeout(atualizarContadorRotas, 100); 
}

function filtrarBaseRelatorio(dId, hId, iniId, fimId, contId) {
    const hS = document.getElementById(hId) ? document.getElementById(hId).value : "TODOS";
    const dS = document.getElementById(dId) ? document.getElementById(dId).value : "TODAS";
    const cP = document.getElementById(contId);
    if (dS === "PERIODO") { if(cP) cP.style.display = 'flex'; } else { if(cP) cP.style.display = 'none'; }
    
    let dF = rotasBrutas;
    if (hS !== "TODOS") dF = dF.filter(r => String(r['Nome da Rota'] || "").toUpperCase().startsWith(hS));
    
    if (dS === "PERIODO") {
        let dI = document.getElementById(iniId) ? document.getElementById(iniId).value : ""; let dFm = document.getElementById(fimId) ? document.getElementById(fimId).value : ""; 
        if (dI || dFm) {
            dF = dF.filter(r => {
                let dr = String(r['Data da rota'] || r['Data'] || ""); if (!dr) return false;
                let pts = dr.split(/[-/ ]/); let dtR;
                if (pts.length >= 3) { if (pts[0].length === 4) dtR = new Date(pts[0], pts[1]-1, pts[2]); else dtR = new Date(pts[2], pts[1]-1, pts[0]); } else return false;
                let mC = true;
                if (dI) { let [y, m, d] = dI.split('-'); if (dtR < new Date(y, m-1, d)) mC = false; }
                if (dFm) { let [y, m, d] = dFm.split('-'); if (dtR > new Date(y, m-1, d)) mC = false; }
                return mC;
            });
        }
    } else if (dS !== "TODAS") dF = dF.filter(r => String(r['Data da rota'] || r['Data'] || "") === dS);
    return dF;
}

function aplicarFiltroUnificadoEstatisticas() {
    const baseFiltrada = filtrarBaseRelatorio('filtro-data-est', 'filtro-hub-est', 'data-ini-est', 'data-fim-est', 'container-periodo-est');
    gerarBI(baseFiltrada);
    limparFiltroMotivoOC();
    gerarOC(baseFiltrada);
    gerarGeralOperacao(baseFiltrada);
}

function aplicarFiltroHubBI() { aplicarFiltroUnificadoEstatisticas(); }
function aplicarFiltroOC() { aplicarFiltroUnificadoEstatisticas(); }

function carregarRotas() {
    fetch(`${API_BASE_URL}/rotas`).then(r=>r.json()).then(d=>{
        if(!d.erro){
            d.forEach(l => { if (l.monitor) l.monitor = String(l.monitor).toUpperCase().trim(); if(l.cores_customizadas) { try { l.coresIndividuais = JSON.parse(l.cores_customizadas); } catch(e) { l.coresIndividuais = {}; } } });
            if (currentUser.hubs !== "TODOS") { const mh = currentUser.hubs.toUpperCase().split(',').map(h=>h.trim()); rotasGlobais = d.filter(r => mh.some(h => (r.nome_rota || "").toUpperCase().includes(h))); } else rotasGlobais = d;
            rotasGlobais = ordenarRotasPorSequencia(rotasGlobais);
            if (document.getElementById('home-stat-rotas')) document.getElementById('home-stat-rotas').innerText = rotasGlobais.length;
            popularDropdownsHubMonitoramento(extrairHubs(rotasGlobais, 'nome_rota')); aplicarFiltroHubPlanilha(); inicializarEscala();
            if (rotasBrutas.length > 0) aplicarFiltroUnificadoEstatisticas();
        }
    });
}

function carregarRelatorios() {
    fetch(`${API_BASE_URL}/rotas_brutas`).then(r=>r.json()).then(d=>{
        if(d.length > 0){
            if (currentUser.hubs !== "TODOS") { const mh = currentUser.hubs.toUpperCase().split(',').map(h=>h.trim()); rotasBrutas = d.filter(r => mh.some(h => String(r["Nome da Rota"] || "").toUpperCase().includes(h))); } else rotasBrutas = d;
            popularDropdownsHubBI(extrairHubs(rotasBrutas, 'Nome da Rota'));
            popularDropdownsDataBI(extrairDatasBI(rotasBrutas));
            aplicarFiltroUnificadoEstatisticas();
        }
    });
}

function atualizarBadgeSessaoOkta(statusLogin, roboOnline) {
    const badge = document.getElementById('badge-status-sessao-okta');
    if (!badge) return;
    if (!roboOnline || statusLogin === 'OFFLINE') {
        badge.style.background = '#7f1d1d';
        badge.style.color = '#fca5a5';
        badge.innerText = '🔴 ROBÔ OFFLINE';
    } else if (statusLogin === 'AGUARDANDO_OKTA') {
        badge.style.background = '#f59e0b';
        badge.style.color = '#0f172a';
        badge.innerText = '📲 AGUARDANDO CÓDIGO OKTA';
    } else if (statusLogin === 'LOGADO') {
        badge.style.background = '#10b981';
        badge.style.color = '#052e16';
        badge.innerText = '🟢 SESSÃO MELI ATIVA';
    } else {
        badge.style.background = '#334155';
        badge.style.color = '#cbd5e1';
        badge.innerText = '⏳ A VERIFICAR SESSÃO...';
    }
}

function enviarCodigoOktaPainel() {
    const inp = document.getElementById('input-codigo-okta');
    if (!inp) return;
    const codigo = inp.value.replace(/\D/g, '').trim();
    if (!codigo || codigo.length < 6) {
        return alert("Digite os 6 dígitos do código Okta Verify!");
    }
    fetch(`${API_BASE_URL}/enviar_codigo_okta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ codigo: codigo })
    }).then(r => r.json()).then(res => {
        if (res.sucesso) {
            inp.value = '';
        } else {
            alert("Erro ao enviar código: " + (res.erro || ""));
        }
    }).catch(() => alert("Erro de conexão com a API."));
}

function reiniciarLoginRoboPainel() {
    fetch(`${API_BASE_URL}/reiniciar_login_robo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    }).then(r => r.json()).then(res => {
        if (!res.sucesso) {
            alert("Não foi possível enviar o comando de reinício.");
        }
    }).catch(() => alert("Erro de conexão com a API."));
}

function iniciarSistemaAposLogin() {
    if (!gridApi) {
        gridApi = agGrid.createGrid(document.querySelector('#myGrid'), gridOptions);
        setTimeout(() => restaurarEstadoColunas(), 100);
    }
    const h = new Date();
    const dH = `${h.getFullYear()}-${String(h.getMonth() + 1).padStart(2, '0')}-${String(h.getDate()).padStart(2, '0')}`;
    document.getElementById('data-escala').value = dH;
    if (document.getElementById('label-data-hoje')) {
        document.getElementById('label-data-hoje').innerText = dH.split('-').reverse().join('/');
    }
    
    carregarMetas();
    carregarEdicoesOcorrencias();
    carregarCoresCustomizadas();
    carregarRotas();
    carregarRelatorios();
    carregarTickets();

    setInterval(carregarTickets, 5000);
    setInterval(atualizarCountdowns, 1000);

    let buscandoLogs = false;
    setInterval(() => {
        if (buscandoLogs) return;
        buscandoLogs = true;

        fetch(`${API_BASE_URL}/ler_logs?ultimo_indice=` + ultimoIndiceLog)
            .then(r => r.json())
            .then(d => {
                buscandoLogs = false;
                const te = document.getElementById('status-robo-label'),
                      le = document.getElementById('status-robo-luz'),
                      ft = document.getElementById('status-robo-text'),
                      sh = document.getElementById('home-stat-robo');

                if (te) te.innerText = d.robo_online ? "ONLINE" : "OFFLINE";
                if (le) {
                    le.style.backgroundColor = d.robo_online ? "#31D086" : "#ef4444";
                    le.style.boxShadow = d.robo_online ? "0 0 5px #31D086" : "0 0 5px #ef4444";
                }
                if (ft) ft.style.color = d.robo_online ? "#31D086" : "#ef4444";
                if (sh) sh.innerHTML = d.robo_online ? '<span style="color:#15803d;">🟢 Robô Online</span>' : '<span style="color:#b91c1c;">🔴 Robô Offline</span>';

                atualizarBadgeSessaoOkta(d.status_login, d.robo_online);

                if (d.logs && d.logs.length > 0) {
                    let aP = false, aB = false;
                    const tm = document.getElementById('terminal-logs');
                    if (tm) {
                        d.logs.forEach(l => {
                            tm.innerHTML += `<span style="color: #31D086;">[${l.hora}] [MOTOR]</span> <span style="color: #e2e8f0;">${l.msg}</span><br>`;
                            if (l.msg.includes("MONITORAMENTO CONCLUÍDO")) aP = true;
                            if (l.msg.includes("RELATÓRIO BAIXADO")) aB = true;
                        });
                        tm.scrollTop = tm.scrollHeight;
                    }
                    ultimoIndiceLog = d.novo_indice;
                    if (aP) carregarRotas();
                    if (aB) carregarRelatorios();
                }
            })
            .catch(() => {
                buscandoLogs = false;
                if (document.getElementById('status-robo-label')) document.getElementById('status-robo-label').innerText = "OFFLINE";
                if (document.getElementById('status-robo-luz')) {
                    document.getElementById('status-robo-luz').style.backgroundColor = "#ef4444";
                    document.getElementById('status-robo-luz').style.boxShadow = "0 0 5px #ef4444";
                }
                if (document.getElementById('status-robo-text')) document.getElementById('status-robo-text').style.color = "#ef4444";
                if (document.getElementById('home-stat-robo')) document.getElementById('home-stat-robo').innerHTML = '<span style="color:#b91c1c;">🔴 Robô Offline</span>';
                atualizarBadgeSessaoOkta('OFFLINE', false);
            });
    }, 2000);
}

// ==========================================
// CORE BI E MAPEAMENTO
// ==========================================
function normalizeBPObj(obj) {
    const n = {};
    for (const k in obj) { let key = k; try { key = decodeURIComponent(escape(k)); } catch(e) {} let val = obj[k]; if (typeof val === 'string') { try { val = decodeURIComponent(escape(val)); } catch(e) {} } n[key] = val; }
    return n;
}

function colMapBI(row) {
    const keys = Object.keys(row || {}); const norm = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]/gi, '').toLowerCase();
    const kN = keys.map(k => ({ raw: k, norm: norm(k) }));
    const findAny = (...pts) => { const ps = pts.map(norm).filter(Boolean); const ex = kN.find(k => ps.includes(k.norm)); if (ex) return ex.raw; const pa = kN.find(k => ps.some(p => k.norm.includes(p) || p.includes(k.norm))); return pa ? pa.raw : ''; };
    return {
        routeId: findAny('ID do Rota', 'ID da Rota', 'IDRota', 'routeid') || keys[0], routeName: findAny('Nome da Rota', 'Nome Rota', 'routeName'), date: findAny('Data da rota', 'Data', 'Datarota'), driverId: findAny('ID do Motorista', 'Motorista', 'driverid'), stopType: findAny('Tipo de parada', 'Tipo parada', 'stoptype'), stop: findAny('Parada', 'Stop'), stopId: findAny('ID de Parada', 'ID da parada', 'IDParada', 'stopid'), schedule: findAny('Horários', 'Horarios', 'Janela', 'Horario', 'Faixa horaria', 'schedule'), ontime: findAny('ONTIME', 'Ontime', 'On Time', 'Status ONTIME', 'status'), punctuality: findAny('Pontualidade', 'Hora real', 'Detalhes'), completedAt: findAny('Hora de coleta concluída', 'Hora de coleta', 'Hora coleta', 'coleta', 'completedat'), packages: findAny('Pacotes coletados', 'Pacotes coletado', 'pacotescol', 'packages'), estimated: findAny('Pacotes estimados', 'Estimado', 'Pacotes previstos', 'estimated'), prepared: findAny('Pacotes preparados', 'Preparados', 'prepared'), routeType: findAny('Tipo do Rota', 'Tipo da Rota', 'Tiporota', 'routetype')
    };
}

function normalizeStatusBI(s, p) {
    const b = String(s || p || '').trim().toLowerCase(); if (!b) return 'unknown';
    if (b.includes('early') || b.includes('antecip')) return 'early'; if (b.includes('late') || b.includes('atras')) return 'late'; if ((b.includes('on') && b.includes('time')) || b === 'ontime' || b === 'on_time') return 'ontime'; return 'unknown';
}

function parseNumberBI(v) {
    if (v == null || v === '') return 0; if (typeof v === 'number') return Number.isFinite(v) ? v : 0;
    let s = String(v).trim().replace(/\s+/g, '');
    if (/^-?\d{1,3}(\.\d{3})*,\d+$/.test(s)) s = s.replace(/\./g, '').replace(',', '.'); else if (/^-?\d{1,3}(,\d{3})*\.\d+$/.test(s)) s = s.replace(/,/g, ''); else s = s.replace(/,/g, '.');
    const n = Number(s); return Number.isFinite(n) ? n : 0;
}

function parseTimeHHMM(t) { if(!t) return null; let m = String(t).match(/(\d{1,2}):(\d{2})/); return m ? parseInt(m[1]) * 60 + parseInt(m[2]) : null; }

function switchBITab(id, btn){
    document.querySelectorAll('#sub-pontualidade .bi-tab-content').forEach(e=>e.classList.remove('active')); document.querySelectorAll('#sub-pontualidade .bi-tab').forEach(e=>e.classList.remove('active'));
    document.getElementById(id).classList.add('active'); if (btn) btn.classList.add('active');
}

function biStatusPill(s) { const m = { early: 'Antecipação', late: 'Atraso', on_time: 'On Time', ontime: 'On Time' }; const c = { early: 'early', late: 'late', on_time: 'ontime', ontime: 'ontime' }; return `<span class="bi-pill ${c[s] || ''}">${m[s] || s}</span>`; }
function setBITbl(id, heads, rows, footer) {
    const t = document.getElementById(id); if (!t) return;
    t.querySelector('thead').innerHTML = `<tr>${heads.map((h, i) => `<th onclick="sortBITable('${id}',${i})">${h} ↕</th>`).join('')}</tr>`;
    t.querySelector('tbody').innerHTML = rows.length ? rows.join('') : `<tr><td colspan="${heads.length}" style="padding:28px;text-align:center;color:#8a9abc;">Nenhum resultado encontrado</td></tr>`;
    const fEl = document.getElementById('bi-footer-' + id.replace('bi-tbl-', '')); if (fEl) fEl.textContent = `${rows.length} linha${rows.length !== 1 ? 's' : ''}` + (footer ? ' · ' + footer : '');
}

function sortBITable(id, col) {
    const k = id + '-' + col; biSortState[k] = !biSortState[k]; const t = document.getElementById(id), tb = t.querySelector('tbody'), r = Array.from(tb.querySelectorAll('tr'));
    r.sort((a, b) => {
        const av = a.cells[col]?.textContent.trim() || '', bv = b.cells[col]?.textContent.trim() || '';
        const an = parseFloat(av.replace(/\./g, '').replace(',', '.')), bn = parseFloat(bv.replace(/\./g, '').replace(',', '.'));
        const cmp = !isNaN(an) && !isNaN(bn) ? an - bn : av.localeCompare(bv, 'pt-BR'); return biSortState[k] ? cmp : -cmp;
    }); r.forEach(x => tb.appendChild(x));
}
function qBI(val, term) { return !term || String(val).toLowerCase().includes(term.toLowerCase()); }

// ==========================================
// GERADOR BI PONTUALIDADE
// ==========================================
function gerarBI(rawRows) {
    if (!rawRows || rawRows.length === 0) {
        document.getElementById('bi-kpis').innerHTML = ''; document.getElementById('bi-meta-bar').style.display = 'none'; document.getElementById('bi-pkgs-panel').style.display = 'none';
        BI_DATA = { raw: [], firstEarly: [], drivers: [], routes: [], clusters: [], routePkgs: {}, pkgsEarly: 0, pkgsLate: 0, pkgsOntime: 0, pkgsTotal: 0 };
        renderBIFirst(); renderBITime(); renderBIDrivers(); renderBIRoutes(); renderBIClusters(); renderBIAll(); return;
    }

    const rows = rawRows.map(normalizeBPObj); const cm = colMapBI(rows[0]);
    const data = rows.map((r, i) => {
        let pSt = normalizeStatusBI(r[cm.ontime], r[cm.punctuality]); let oM = 0;
        if (pSt === 'early' || pSt === 'late') {
            let scP = String(r[cm.schedule] || '').split(' a ');
            if (scP.length >= 2) {
                let sS = parseTimeHHMM(scP[0]), sE = parseTimeHHMM(scP[1]), sR = parseTimeHHMM(r[cm.punctuality] || r[cm.ontime]);
                if (sS !== null && sE !== null && sR !== null) { if (pSt === 'early' && sR < sS) oM = sS - sR; else if (pSt === 'late' && sR > sE) oM = sR - sE; }
            }
        }
        return { idx: i, routeId: String(r[cm.routeId] || '').trim(), routeName: String(r[cm.routeName] || '').trim(), date: String(r[cm.date] || '').trim(), driverId: String(r[cm.driverId] || '').trim(), stopType: String(r[cm.stopType] || '').trim(), stop: String(r[cm.stop] || '').trim(), stopId: String(r[cm.stopId] || '').trim(), schedule: String(r[cm.schedule] || '').trim(), ontime: pSt, punctuality: String(r[cm.punctuality] || '').trim(), completedAt: String(r[cm.completedAt] || '').trim(), packages: parseNumberBI(r[cm.packages]), estimated: parseNumberBI(r[cm.estimated]), prepared: parseNumberBI(r[cm.prepared]), routeType: String(r[cm.routeType] || '').trim(), outOfWindowMins: oM };
    }).filter(d => d.routeId || d.routeName || d.driverId || d.stop || d.packages || d.estimated || d.prepared);

    data.forEach(d => { const p = d.routeName.split('_'); d.cluster = (p.length >= 2 && p[1]) ? p[1] : 'N/A'; });
    const dFilt = data;

    function ps(s) { if (!s) return 9999; const m = String(s).match(/(\d{1,2}):(\d{2})/); return m ? parseInt(m[1]) * 60 + parseInt(m[2]) : 9999; }
    const bR = {}; dFilt.forEach(d => { (bR[d.routeId] = bR[d.routeId] || []).push(d); });
    Object.values(bR).forEach(st => { st.sort((a, b) => ps(a.schedule) - ps(b.schedule)); st.forEach((d, i) => { d.stopNum = i + 1; }); });

    const fE = dFilt.filter(d => d.stopNum === 1 && d.ontime === 'early');
    const dMap = {};
    dFilt.forEach(d => {
        if (!dMap[d.driverId]) dMap[d.driverId] = { driverId: d.driverId, total: 0, early: 0, late: 0, ontime: 0, pkgsEarly: 0, pkgsLate: 0, pkgsOntime: 0, routes: new Set(), firstEarly: 0 };
        const dm = dMap[d.driverId]; dm.total++; dm.routes.add(d.routeId);
        if (d.ontime === 'early') { dm.early++; dm.pkgsEarly += d.packages || 0; } else if (d.ontime === 'late') { dm.late++; dm.pkgsLate += d.packages || 0; } else { dm.ontime++; dm.pkgsOntime += d.packages || 0; }
    });
    fE.forEach(d => { if (dMap[d.driverId]) dMap[d.driverId].firstEarly++; });
    const drvs = Object.values(dMap).map(d => ({ ...d, routes: d.routes.size, pctEarly: +(d.early / d.total * 100).toFixed(1), pctLate: +(d.late / d.total * 100).toFixed(1), pctOntime: +(d.ontime / d.total * 100).toFixed(1) })).filter(d => d.early > 0 || d.late > 0).sort((a, b) => b.early - a.early || b.late - a.late);

    const rMap = {};
    dFilt.forEach(d => {
        if (d.ontime !== 'early' && d.ontime !== 'late') return;
        if (!rMap[d.routeId]) rMap[d.routeId] = { routeId: d.routeId, routeName: d.routeName, date: d.date, driverId: d.driverId, total: 0, early: 0, late: 0, packages: 0, pkgsEarly: 0, pkgsLate: 0 };
        rMap[d.routeId].total++; rMap[d.routeId].packages += d.packages || 0;
        if (d.ontime === 'early') { rMap[d.routeId].early++; rMap[d.routeId].pkgsEarly += d.packages || 0; } else { rMap[d.routeId].late++; rMap[d.routeId].pkgsLate += d.packages || 0; }
    });

    const rPkgs = {}; dFilt.forEach(d => { rPkgs[d.routeId] = (rPkgs[d.routeId] || 0) + (d.packages || 0); });
    Object.values(rMap).forEach(r => { r.packages = rPkgs[r.routeId] || 0; });
    const rts = Object.values(rMap).sort((a, b) => b.early - a.early || b.late - a.late);

    const cMap = {};
    dFilt.forEach(d => {
        const cl = d.cluster || 'N/A';
        if (!cMap[cl]) cMap[cl] = { cluster: cl, total: 0, early: 0, late: 0, ontime: 0, routes: new Set(), pkgsEarly: 0, pkgsLate: 0, pkgsOntime: 0, firstEarlyCount: 0 };
        const cm2 = cMap[cl]; cm2.total++; cm2.routes.add(d.routeId);
        if (d.ontime === 'early') { cm2.early++; cm2.pkgsEarly += d.packages || 0; } else if (d.ontime === 'late') { cm2.late++; cm2.pkgsLate += d.packages || 0; } else { cm2.ontime++; cm2.pkgsOntime += d.packages || 0; }
    });
    fE.forEach(d => { const cl = d.cluster || 'N/A'; if (cMap[cl]) cMap[cl].firstEarlyCount++; });
    const clus = Object.values(cMap).map(c => {
        const pT = (c.pkgsOntime || 0) + (c.pkgsEarly || 0) + (c.pkgsLate || 0);
        return { ...c, routes: c.routes.size, pkgsTotal: pT, pctOntime: +(pT > 0 ? c.pkgsOntime / pT * 100 : 0).toFixed(1), pctEarly: +(pT > 0 ? c.pkgsEarly / pT * 100 : 0).toFixed(1), pctLate: +(pT > 0 ? c.pkgsLate / pT * 100 : 0).toFixed(1) };
    }).sort((a, b) => b.early - a.early || b.late - a.late);

    const tot = dFilt.length; const dN = new Set(dFilt.map(d => d.driverId).filter(Boolean)).size; const rN = new Set(dFilt.map(d => d.routeId).filter(Boolean)).size;
    const pE = dFilt.filter(d => d.ontime === 'early').reduce((s, d) => s + d.packages, 0), pL = dFilt.filter(d => d.ontime === 'late').reduce((s, d) => s + d.packages, 0), pO = dFilt.filter(d => d.ontime === 'ontime').reduce((s, d) => s + d.packages, 0), pT = dFilt.reduce((s, d) => s + (d.packages || 0), 0);

    BI_DATA = { raw: dFilt, firstEarly: fE, drivers: drvs, routes: rts, clusters: clus, routePkgs: rPkgs, pkgsEarly: pE, pkgsLate: pL, pkgsOntime: pO, pkgsTotal: pT };

    const kpis = [
        { label: 'ROTAS', val: rN, cls: 'blue' }, { label: 'TOTAL PACOTES', val: pT.toLocaleString('pt-BR'), cls: 'blue' },
        { label: 'TOTAL EM ONTIME', val: pO.toLocaleString('pt-BR'), cls: 'ontime' }, { label: 'ANTECIPAÇÕES DE PACOTES', val: pE.toLocaleString('pt-BR'), cls: 'early' },
        { label: 'ATRASO DE PACOTES', val: pL.toLocaleString('pt-BR'), cls: 'late' }, { label: 'ROTAS COM ANTECIPAÇÃO', val: new Set(rts.filter(r => r.early > 0).map(r => r.routeId)).size, cls: 'early' },
        { label: '1ª PARADA ANTECIPADA', val: fE.length, cls: 'alert' }, { label: 'QUANTIDADE DE PARADAS', val: tot, cls: 'blue' }, { label: 'QUANTIDADE DE MOTORISTAS', val: dN, cls: 'blue' }
    ];
    document.getElementById('bi-kpis').innerHTML = kpis.map(k => `<div class="bi-kpi ${k.cls}"><label>${k.label}</label><div class="val">${k.val}</div></div>`).join('');

    const hS = obterHubFiltroEstatisticas();
    const T_ON = obterMetaAtiva(hS);
    const T_EA = 10;
    const pctO = pT > 0 ? +(pO / pT * 100).toFixed(1) : 0, pctE = pT > 0 ? +(pE / pT * 100).toFixed(1) : 0;
    const okO = pctO >= T_ON, okE = pctE <= T_EA;

    let bE = "";
    if (podeConfigurarMeta()) {
        bE = `<span style="cursor:pointer; margin-left: 8px; font-size: 14px;" onclick="editarMetaHub('${hS}')" title="Configurar Meta do HUB / Mês">✏️</span>`;
    }

    const mB = document.getElementById('bi-meta-bar');
    if (mB) {
        mB.style.display = 'grid';
        const oF = Math.min(pctO / T_ON * 100, 100).toFixed(1), eF = Math.min(pctE / T_EA * 100, 100).toFixed(1);
        const ymLabel = obterAnoMesAtivoFiltro().split('-').reverse().join('/');
        document.getElementById('bi-meta-ontime').innerHTML = `<div class="bi-meta-icon" style="background:${okO ? '#f0fdf4' : '#fef2f2'}">${okO ? '✅' : '⚠️'}</div><div class="bi-meta-info"><label>Meta On Time (${hS} · Ref: ${ymLabel})</label><div class="bi-meta-vals"><span class="bi-meta-atual" style="color:${okO ? '#15803d' : '#b91c1c'}">${pctO.toFixed(1)}% <span style="font-size:12px; font-weight:normal; color:#4a5a7a; margin-left: 5px;">(Margem -2%: ${(pctO - 2).toFixed(1)}%)</span></span><span class="bi-meta-target" style="margin-left:auto;">target: ${T_ON}% ${bE}</span><span class="bi-meta-status" style="background:${okO ? '#f0fdf4' : '#fef2f2'};color:${okO ? '#15803d' : '#b91c1c'}">${okO ? 'Atingido' : 'Abaixo'}</span></div><div class="bi-meta-bar-track"><div class="bi-meta-bar-fill" style="width:${oF}%;background:${okO ? '#22c55e' : '#ef4444'}"></div></div></div>`;
        document.getElementById('bi-meta-early').innerHTML = `<div class="bi-meta-icon" style="background:${okE ? '#f0fdf4' : '#fff7ed'}">${okE ? '✅' : '⚡'}</div><div class="bi-meta-info"><label>Meta máxima fora da janela de pacotes coletados</label><div class="bi-meta-vals"><span class="bi-meta-atual" style="color:${okE ? '#15803d' : '#c2410c'}">${pctE.toFixed(1)}%</span><span class="bi-meta-target" style="margin-left:auto;">limite: ${T_EA}%</span><span class="bi-meta-status" style="background:${okE ? '#f0fdf4' : '#fff7ed'};color:${okE ? '#15803d' : '#c2410c'}">${okE ? 'OK' : 'Excedido'}</span></div><div class="bi-meta-bar-track"><div class="bi-meta-bar-fill" style="width:${eF}%;background:${okE ? '#22c55e' : '#f97316'}"></div></div></div>`;
    }
    renderBIPkgsPanel(); renderBIFirst(); renderBITime(); renderBIDrivers(); renderBIRoutes(); renderBIClusters(); renderBIAll();
}

function renderBIPkgsPanel() {
    const { pkgsEarly, pkgsLate, pkgsOntime, pkgsTotal } = BI_DATA; const pnl = document.getElementById('bi-pkgs-panel');
    if (!pnl) return; if (pkgsTotal === 0) { pnl.style.display = 'none'; return; }
    const pO = pkgsTotal ? Math.round(pkgsOntime / pkgsTotal * 100) : 0, pE = pkgsTotal ? Math.round(pkgsEarly  / pkgsTotal * 100) : 0, pL = 100 - pO - pE;
    pnl.style.display = 'block';
    pnl.innerHTML = `<h3>📦 Detalhes de pacotes por pontualidade separadamente</h3><div style="font-size:11px;color:#4a5a7a;margin:-6px 0 12px 0">Cálculo com base nos pacotes coletados em geral da base.</div><div class="bi-pkgs-grid"><div class="bi-pkgs-stat ontime"><label>✅ On Time</label><div class="pv">${pkgsOntime.toLocaleString('pt-BR')}</div><div class="pp">${pO}% dos pcts</div></div><div class="bi-pkgs-stat early"><label>⚡ Antecipados</label><div class="pv">${pkgsEarly.toLocaleString('pt-BR')}</div><div class="pp">${pE}% dos pcts</div></div><div class="bi-pkgs-stat late"><label>⏰ Atrasados</label><div class="pv">${pkgsLate.toLocaleString('pt-BR')}</div><div class="pp">${pL}% dos pcts</div></div></div><div style="margin-top:4px"><div style="font-size:11px;color:#4a5a7a;margin-bottom:6px">Total: <strong>${pkgsTotal.toLocaleString('pt-BR')} pacotes</strong></div><div class="bi-stacked-bar"><div style="width:${pO}%;background:#22c55e"></div><div style="width:${pE}%;background:#f97316"></div><div style="width:${Math.max(pL, 0)}%;background:#ef4444"></div></div><div class="bi-stacked-legend"><span class="leg-ontime">On Time — ${pO}%</span><span class="leg-early">Antecipado — ${pE}%</span><span class="leg-late">Atrasado — ${pL}%</span></div></div>`;
}

function renderBIFirst() {
    const term = document.getElementById('bi-search-first') ? document.getElementById('bi-search-first').value : '';
    const rows = (BI_DATA.firstEarly || []).filter(d => (qBI(d.routeId, term) || qBI(d.routeName, term) || qBI(d.driverId, term) || qBI(d.stop, term)));
    const rHTML = rows.map(d => `<tr><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.routeId}</td><td>${d.routeName}</td><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.date}</td><td style="font-family:monospace;color:#0d1b3e;font-weight:700">${d.driverId}</td><td>${d.stop}</td><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.schedule}</td><td><span class="bi-pill early">${d.punctuality || 'early'}</span></td><td><span style="font-weight:700;color:#0d1b3e">${((BI_DATA.routePkgs && BI_DATA.routePkgs[d.routeId]) || 0).toLocaleString('pt-BR')}</span></td></tr>`);
    const al = document.getElementById('bi-alert-first');
    if (al) { if ((BI_DATA.firstEarly || []).length > 0) al.innerHTML = `<div class="bi-alert-banner">⚠️ <div><strong>${BI_DATA.firstEarly.length} ocorrência${BI_DATA.firstEarly.length > 1 ? 's' : ''}</strong> de antecipação na primeira parada detetada${BI_DATA.firstEarly.length > 1 ? 's' : ''} — motoristas chegaram antes da janela horária na 1ª coleta da rota.</div></div>`; else al.innerHTML = `<div class="bi-alert-banner" style="background:#f0fdf4;border-color:#bbf7d0;color:#15803d">✅ Nenhuma antecipação na primeira parada encontrada.</div>`; }
    setBITbl('bi-tbl-first', ['ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Parada (1ª)', 'Janela', 'Hora Real', '📦 Pacotes Rota'], rHTML, `de ${(BI_DATA.firstEarly || []).length} total`);
}

function renderBITime() {
    const term = document.getElementById('bi-search-time') ? document.getElementById('bi-search-time').value : '', filt = document.getElementById('bi-filter-time') ? document.getElementById('bi-filter-time').value : 'all';
    let rows = (BI_DATA.raw || []).filter(d => {
        if (d.ontime !== 'early' && d.ontime !== 'late') return false; if (!qBI(d.routeId, term) && !qBI(d.routeName, term) && !qBI(d.driverId, term) && !qBI(d.stop, term)) return false;
        let m = d.outOfWindowMins || 0; if (filt === '0-5' && (m < 0 || m > 5)) return false; if (filt === '5-15' && (m <= 5 || m > 15)) return false; if (filt === '15-30' && (m <= 15 || m > 30)) return false; if (filt === '30-60' && (m <= 30 || m > 60)) return false; if (filt === '60+' && m <= 60) return false; return true;
    });
    const rHTML = rows.map(d => { let m = d.outOfWindowMins || 0; let bC = m <= 5 ? 'bi-time-0' : m <= 15 ? 'bi-time-1' : m <= 30 ? 'bi-time-2' : m <= 60 ? 'bi-time-3' : 'bi-time-4'; return `<tr><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.routeId}</td><td>${d.routeName}</td><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.date}</td><td style="font-family:monospace;color:#0d1b3e;font-weight:700">${d.driverId}</td><td>${d.stop}</td><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.schedule}</td><td style="font-family:monospace;font-size:11px;color:#0d1b3e;font-weight:600">${d.punctuality}</td><td>${biStatusPill(d.ontime)}</td><td><span class="bi-time-badge ${bC}">${m} min</span></td></tr>`; });
    setBITbl('bi-tbl-time', ['ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Parada', 'Janela', 'Hora Real', 'Status', 'TEMPO FORA'], rHTML, `${rows.length} ocorrências`);
}

function renderBIDrivers() {
    const term = document.getElementById('bi-search-drivers') ? document.getElementById('bi-search-drivers').value : '', filt = document.getElementById('bi-filter-drivers') ? document.getElementById('bi-filter-drivers').value : 'all', hS = obterHubFiltroEstatisticas();
    const T_ON = obterMetaAtiva(hS);
    let rows = (BI_DATA.drivers || []).filter(d => { if (!qBI(d.driverId, term)) return false; if (filt === 'early') return d.early > 0; if (filt === 'late') return d.late > 0; if (filt === 'first') return d.firstEarly > 0; return true; });
    const rHTML = rows.map((d, i) => {
        const eBg = d.early >= 15 ? '#fef2f2' : d.early >= 8 ? '#fff7ed' : '#fffbeb'; const eC  = d.early >= 15 ? '#b91c1c' : d.early >= 8 ? '#c2410c' : '#92400e'; const s1 = d.firstEarly > 0 ? `<span style="color:#f97316;font-size:13px">★</span> ${d.firstEarly}` : '-';
        const pE = d.total > 0 ? +(d.early  / d.total * 100).toFixed(1) : 0, pO = d.total > 0 ? +(d.ontime / d.total * 100).toFixed(1) : 0; const okE = pE <= 10, okO = pO >= T_ON;
        return `<tr><td style="color:#8a9abc;font-size:11px">${i + 1}</td><td style="font-family:monospace;color:#0d1b3e;font-weight:700">${d.driverId}</td><td>${d.routes}</td><td>${d.total}</td><td><span style="background:${eBg};color:${eC};padding:2px 8px;border-radius:10px;font-weight:600;font-size:12px">${d.early}</span></td><td>${d.late > 0 ? `<span style="background:#fef2f2;color:#b91c1c;padding:2px 8px;border-radius:10px;font-weight:600;font-size:12px">${d.late}</span>` : '<span style="color:#8a9abc">-</span>'}</td><td>${d.ontime}</td><td style="font-weight:600;color:#c2410c">${(d.pkgsEarly || 0).toLocaleString('pt-BR')}</td><td style="font-weight:600;color:#b91c1c">${(d.pkgsLate || 0).toLocaleString('pt-BR')}</td><td style="font-weight:600;color:#15803d">${(d.pkgsOntime || 0).toLocaleString('pt-BR')}</td><td style="font-weight:600;color:${okE ? '#15803d' : '#c2410c'}">${pE.toFixed(1)}%</td><td style="font-weight:600;color:${okO ? '#15803d' : '#b91c1c'}">${pO.toFixed(1)}%</td><td><span style="background:${okE ? '#f0fdf4' : '#fff7ed'};color:${okE ? '#15803d' : '#c2410c'};padding:2px 8px;border-radius:10px;font-weight:600;font-size:11px">${okE ? 'OK' : 'Excedido'}</span></td><td><span style="background:${okO ? '#f0fdf4' : '#fef2f2'};color:${okO ? '#15803d' : '#b91c1c'};padding:2px 8px;border-radius:10px;font-weight:600;font-size:11px">${okO ? 'Atingido' : 'Abaixo'}</span></td><td style="font-size:12px">${s1}</td></tr>`;
    });
    setBITbl('bi-tbl-drivers', ['#', 'ID Motorista', 'Rotas', 'Total Paradas', '⚡ Antecip.', '⏰ Atrasos', '✅ On Time', '📦 Pcts Antecip.', '📦 Pcts Atraso', '📦 Pcts On Time', '% Antecip.', '% On Time', 'Meta Antecip.', 'Meta OnTime', '1ª Parada'], rHTML, `${(BI_DATA.drivers || []).length} motoristas com ocorrências`);
}

function renderBIRoutes() {
    const term = document.getElementById('bi-search-routes') ? document.getElementById('bi-search-routes').value : '', filt = document.getElementById('bi-filter-routes') ? document.getElementById('bi-filter-routes').value : 'all';
    let rows = (BI_DATA.routes || []).filter(d => { if (filt === 'early' && d.early === 0) return false; if (filt === 'late' && d.late === 0) return false; return qBI(d.routeId, term) || qBI(d.routeName, term) || qBI(d.driverId, term); });
    const rHTML = rows.map(d => {
        const eBg = d.early >= 8 ? '#fef2f2' : d.early >= 5 ? '#fff7ed' : '#fffbeb'; const eC  = d.early >= 8 ? '#b91c1c' : d.early >= 5 ? '#c2410c' : '#92400e';
        return `<tr><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.routeId}</td><td>${d.routeName}</td><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.date}</td><td style="font-family:monospace;color:#0d1b3e;font-weight:700">${d.driverId}</td><td>${d.total}</td><td>${d.early > 0 ? `<span style="background:${eBg};color:${eC};padding:2px 8px;border-radius:10px;font-weight:600;font-size:12px">${d.early}</span>` : '-'}</td><td>${d.late > 0 ? `<span style="background:#fef2f2;color:#b91c1c;padding:2px 8px;border-radius:10px;font-weight:600;font-size:12px">${d.late}</span>` : '-'}</td><td style="font-weight:600;color:#c2410c">${(d.pkgsEarly || 0).toLocaleString('pt-BR')}</td><td style="font-weight:600;color:#b91c1c">${(d.pkgsLate || 0).toLocaleString('pt-BR')}</td><td style="font-weight:700;color:#0d1b3e">${(d.packages || 0).toLocaleString('pt-BR')}</td></tr>`;
    });
    setBITbl('bi-tbl-routes', ['ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Paradas Off.', '⚡ Antecip.', '⏰ Atrasos', '📦 Pcts Antecip.', '📦 Pcts Atraso', '📦 Total Rota'], rHTML, `de ${(BI_DATA.routes || []).length} rotas com ocorrências`);
}

function setBIClusterView(m) {
    document.getElementById('bi-cluster-cards-view').style.display = m === 'cards' ? '' : 'none'; document.getElementById('bi-cluster-table-view').style.display = m === 'table' ? '' : 'none';
    document.getElementById('bi-view-cards').classList.toggle('active', m === 'cards'); document.getElementById('bi-view-table').classList.toggle('active', m === 'table');
}

function renderBIClusters() {
    const term = document.getElementById('bi-search-clusters') ? document.getElementById('bi-search-clusters').value.toLowerCase() : '', sortBy = document.getElementById('bi-sort-clusters') ? document.getElementById('bi-sort-clusters').value : 'early';
    let list = (BI_DATA.clusters || []).filter(c => !term || c.cluster.toLowerCase().includes(term));
    if (sortBy === 'early') list.sort((a, b) => b.early - a.early); else if (sortBy === 'late') list.sort((a, b) => b.late - a.late); else if (sortBy === 'ontime_asc') list.sort((a, b) => a.pctOntime - b.pctOntime); else if (sortBy === 'ontime_desc') list.sort((a, b) => b.pctOntime - a.pctOntime); else if (sortBy === 'name') list.sort((a, b) => a.cluster.localeCompare(b.cluster));

    const grid = document.getElementById('bi-clusters-grid'), hS = obterHubFiltroEstatisticas(), T_ON = obterMetaAtiva(hS), T_EA = 10;
    if (grid) {
        grid.innerHTML = list.map(c => {
            const ok = c.pctOntime >= T_ON, bd = c.pctEarly > T_EA, cls = ok ? 'ok' : (bd ? 'warn' : 'bad'), mc = ok ? '#15803d' : (bd ? '#c2410c' : '#b91c1c'), po = Math.max(c.pctOntime, 0), pe = Math.max(c.pctEarly, 0), pl = Math.max(c.pctLate, 0);
            return `<div class="bi-cluster-card ${cls}"><div class="bi-cluster-name">📍 ${c.cluster}</div><div class="bi-cluster-routes">${c.routes} rota${c.routes !== 1 ? 's' : ''} · ${c.total} paradas · ${c.pkgsTotal.toLocaleString('pt-BR')} pcts</div><div class="bi-cluster-pct-main" style="color:${mc}">${c.pctOntime.toFixed(1)}%</div><div class="bi-cluster-pct-label">On Time por pcts ${ok ? '✅' : '⚠️'} · meta: ${T_ON}%</div><div class="bi-cluster-bar"><div style="width:${po}%;background:#22c55e"></div><div style="width:${pe}%;background:#f97316"></div><div style="width:${pl}%;background:#ef4444"></div></div><div class="bi-cluster-stats"><div class="bi-cluster-stat g"><div class="sv">${c.pkgsOntime.toLocaleString('pt-BR')}</div><div class="sl">Pcts OT</div></div><div class="bi-cluster-stat o"><div class="sv">${c.pkgsEarly.toLocaleString('pt-BR')}</div><div class="sl">Pcts Antecip.</div></div><div class="bi-cluster-stat r"><div class="sv">${c.pkgsLate.toLocaleString('pt-BR')}</div><div class="sl">Pcts Atraso</div></div></div>${c.firstEarlyCount > 0 ? `<div style="margin-top:8px;font-size:11px;color:#c2410c;font-weight:600">⚡ ${c.firstEarlyCount} antecip. na 1ª parada</div>` : ''}</div>`;
        }).join('') || `<div style="padding:32px;text-align:center;color:#8a9abc">Nenhum cluster encontrado</div>`;
    }
    const rHTML = list.map(c => { const ok = c.pctOntime >= T_ON, bd = c.pctEarly > T_EA; return `<tr><td style="font-weight:700;color:#0d1b3e">📍 ${c.cluster}</td><td>${c.routes}</td><td>${c.total}</td><td style="color:#15803d;font-weight:600">${c.ontime}</td><td style="color:#c2410c;font-weight:600">${c.early}</td><td style="color:#b91c1c;font-weight:600">${c.late}</td><td><span style="font-weight:700;color:${ok ? '#15803d' : '#b91c1c'}">${c.pctOntime.toFixed(1)}%</span></td><td><span style="font-weight:700;color:${bd ? '#c2410c' : '#15803d'}">${c.pctEarly.toFixed(1)}%</span></td><td><span style="font-weight:700;color:${c.pctLate > 0 ? '#b91c1c' : '#15803d'}">${c.pctLate.toFixed(1)}%</span></td><td style="font-weight:600">${c.pkgsOntime.toLocaleString('pt-BR')}</td><td style="color:#c2410c;font-weight:600">${c.firstEarlyCount}</td><td><span style="background:${ok ? '#f0fdf4' : '#fef2f2'};color:${ok ? '#15803d' : '#b91c1c'};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">${ok ? 'Atingido' : 'Abaixo'}</span></td></tr>`; });
    setBITbl('bi-tbl-clusters', ['Cluster', 'Rotas', 'Paradas', '✅ On Time', '⚡ Antecip.', '⏰ Atraso', '% On Time (pcts)', '% Antecip. (pcts)', '% Atraso (pcts)', '📦 Pcts OT', '1ª Antecip.', 'Status Meta'], rHTML, `${list.length} cluster${list.length !== 1 ? 's' : ''}`);
}

function renderBIAll() {
    const term = document.getElementById('bi-search-all') ? document.getElementById('bi-search-all').value : '', filt = document.getElementById('bi-filter-all') ? document.getElementById('bi-filter-all').value : 'offenders';
    let rows = (BI_DATA.raw || []).filter(d => {
        if (filt === 'early' && d.ontime !== 'early') return false; if (filt === 'late' && d.ontime !== 'late') return false; if (filt === 'offenders' && d.ontime !== 'early' && d.ontime !== 'late') return false;
        return qBI(d.routeId, term) || qBI(d.routeName, term) || qBI(d.driverId, term) || qBI(d.stop, term);
    });
    const rHTML = rows.slice(0, 500).map(d => `<tr><td style="color:#8a9abc;font-size:11px">${d.stopNum}</td><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.routeId}</td><td>${d.routeName}</td><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.date}</td><td style="font-family:monospace;color:#0d1b3e;font-weight:700">${d.driverId}</td><td>${d.stop}</td><td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.schedule}</td><td>${biStatusPill(d.ontime)}</td><td style="font-weight:700;color:#0d1b3e">${d.packages > 0 ? d.packages.toLocaleString('pt-BR') : '<span style="color:#8a9abc">—</span>'}</td><td style="font-size:11px;color:#4a5a7a">${d.punctuality}</td></tr>`);
    setBITbl('bi-tbl-all', ['#', 'ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Parada', 'Janela', 'Status', '📦 Pacotes', 'Detalhes'], rHTML, rows.length > 500 ? `mostrando 500 de ${rows.length}` : `${rows.length} paradas`);
}

function exportarEstratificacao(tipo) {
    if (!BI_DATA.raw || !BI_DATA.raw.length) return alert("Não existem dados carregados para exportar.");
    const hS = obterHubFiltroEstatisticas();
    const T_ON = obterMetaAtiva(hS);
    const wb = XLSX.utils.book_new();
    let dadosAba = [];
    let nomeAba = "Estratificacao";

    if (tipo === 'first') {
        nomeAba = "1a_Parada_Antecipada";
        dadosAba.push(['ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Parada (1ª)', 'Janela', 'Hora Real', 'Pacotes Rota']);
        BI_DATA.firstEarly.forEach(d => dadosAba.push([d.routeId, d.routeName, d.date, d.driverId, d.stop, d.schedule, d.punctuality, BI_DATA.routePkgs[d.routeId] || 0]));
    } else if (tipo === 'time') {
        nomeAba = "Tempo_Fora_Janela";
        dadosAba.push(['ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Parada', 'Janela', 'Hora Real', 'Status', 'Tempo Fora (min)']);
        BI_DATA.raw.filter(d => d.ontime === 'early' || d.ontime === 'late').forEach(d => dadosAba.push([d.routeId, d.routeName, d.date, d.driverId, d.stop, d.schedule, d.punctuality, d.ontime, d.outOfWindowMins || 0]));
    } else if (tipo === 'drivers') {
        nomeAba = "Ranking_Motoristas";
        dadosAba.push(['#', 'ID Motorista', 'Rotas', 'Total Paradas', 'Antecipações', 'Atrasos', 'On Time', 'Pcts Antecip.', 'Pcts Atraso', 'Pcts On Time', '% Antecip.', '% On Time', 'Meta Antecip.', 'Meta OnTime', 'Antecip. 1ª Parada']);
        BI_DATA.drivers.forEach((d, i) => {
            const pe = d.total ? (d.early / d.total * 100).toFixed(1) : 0;
            const po = d.total ? (d.ontime / d.total * 100).toFixed(1) : 0;
            dadosAba.push([i + 1, d.driverId, d.routes, d.total, d.early, d.late, d.ontime, d.pkgsEarly || 0, d.pkgsLate || 0, d.pkgsOntime || 0, pe + '%', po + '%', pe <= 10 ? 'OK' : 'Excedido', po >= T_ON ? 'Atingido' : 'Abaixo', d.firstEarly]);
        });
    } else if (tipo === 'routes') {
        nomeAba = "Rotas_Ofensoras";
        dadosAba.push(['ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Paradas Off.', 'Antecipações', 'Atrasos', 'Pcts Antecip.', 'Pcts Atraso', 'Total Pacotes']);
        BI_DATA.routes.forEach(d => dadosAba.push([d.routeId, d.routeName, d.date, d.driverId, d.total, d.early, d.late, d.pkgsEarly || 0, d.pkgsLate || 0, d.packages]));
    } else if (tipo === 'clusters') {
        nomeAba = "Por_Cluster";
        dadosAba.push(['Cluster', 'Rotas', 'Total Paradas', 'On Time', 'Antecipações', 'Atrasos', '% On Time (pcts)', '% Antecip. (pcts)', '% Atraso (pcts)', 'Pcts On Time', 'Pcts Antecip.', 'Pcts Atraso', 'Antecip. 1ª Parada', 'Meta OnTime']);
        BI_DATA.clusters.forEach(c => dadosAba.push([c.cluster, c.routes, c.total, c.ontime, c.early, c.late, c.pctOntime + '%', c.pctEarly + '%', c.pctLate + '%', c.pkgsOntime || 0, c.pkgsEarly || 0, c.pkgsLate || 0, c.firstEarlyCount, c.pctOntime >= T_ON ? 'Atingido' : 'Abaixo']));
    } else {
        nomeAba = "Todas_Paradas";
        dadosAba.push(['Nº Parada', 'ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Parada', 'Janela', 'Status', 'Pacotes', 'Detalhe']);
        BI_DATA.raw.forEach(d => dadosAba.push([d.stopNum, d.routeId, d.routeName, d.date, d.driverId, d.stop, d.schedule, d.ontime, d.packages || 0, d.punctuality]));
    }

    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(dadosAba), nomeAba);
    XLSX.writeFile(wb, `Estratificacao_${nomeAba}_${hS}_${new Date().toISOString().split('T')[0]}.xlsx`);
}

function adicionarAbasPontualidadeNoWorkbook(wb, hS) {
    const T_ON = obterMetaAtiva(hS);
    const t = BI_DATA.raw.length, eN = BI_DATA.raw.filter(d => d.ontime === 'early').length, lN = BI_DATA.raw.filter(d => d.ontime === 'late').length, oN = BI_DATA.raw.filter(d => d.ontime === 'on_time' || d.ontime === 'ontime').length;
    const pT = BI_DATA.pkgsTotal || 0, pE = pT ? (BI_DATA.pkgsEarly / pT * 100).toFixed(1) : 0, pO = pT ? (BI_DATA.pkgsOntime / pT * 100).toFixed(1) : 0;
    const resumo = [['Indicador', 'Valor', 'Meta', 'Status'], ['% On Time (base pcts)', pO + '%', T_ON + '%', pO >= T_ON ? 'Atingido' : 'Abaixo'], ['% Antecipação (base pcts)', pE + '%', '≤ 10%', pE <= 10 ? 'OK' : 'Excedido'], ['Total de Paradas', t, '', ''], ['Antecipações (paradas)', eN, '', ''], ['Atrasos (paradas)', lN, '', ''], ['On Time (paradas)', oN, '', ''], ['1ª Parada Antecipada', BI_DATA.firstEarly.length, '', ''], ['Pacotes On Time', BI_DATA.pkgsOntime || 0, '', ''], ['Pacotes Antecipados', BI_DATA.pkgsEarly || 0, '', ''], ['Pacotes Atrasados', BI_DATA.pkgsLate || 0, '', '']];
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(resumo), 'Resumo Pontualidade');
    const fR = [['ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Parada (1ª)', 'Janela', 'Hora Real', 'Pacotes Rota']]; BI_DATA.firstEarly.forEach(d => fR.push([d.routeId, d.routeName, d.date, d.driverId, d.stop, d.schedule, d.punctuality, BI_DATA.routePkgs[d.routeId] || 0])); XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(fR), '1ª Parada Antecipada');
    const tR = [['ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Parada', 'Janela', 'Hora Real', 'Status', 'Tempo Fora (min)']]; BI_DATA.raw.filter(d => d.ontime === 'early' || d.ontime === 'late').forEach(d => { tR.push([d.routeId, d.routeName, d.date, d.driverId, d.stop, d.schedule, d.punctuality, d.ontime, d.outOfWindowMins || 0]); }); XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(tR), 'Tempo Fora da Janela');
    const dR = [['#', 'ID Motorista', 'Rotas', 'Total Paradas', 'Antecipações', 'Atrasos', 'On Time', 'Pcts Antecip.', 'Pcts Atraso', 'Pcts On Time', '% Antecip.', '% On Time', 'Meta Antecip.', 'Meta OnTime', 'Antecip. 1ª Parada']]; BI_DATA.drivers.forEach((d, i) => { const pe = d.total ? (d.early / d.total * 100).toFixed(1) : 0; const po = d.total ? (d.ontime / d.total * 100).toFixed(1) : 0; dR.push([i + 1, d.driverId, d.routes, d.total, d.early, d.late, d.ontime, d.pkgsEarly || 0, d.pkgsLate || 0, d.pkgsOntime || 0, pe + '%', po + '%', pe <= 10 ? 'OK' : 'Excedido', po >= T_ON ? 'Atingido' : 'Abaixo', d.firstEarly]); }); XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(dR), 'Ranking Motoristas');
    const rtR = [['ID Rota', 'Nome da Rota', 'Data', 'ID Motorista', 'Paradas Off.', 'Antecipações', 'Atrasos', 'Pcts Antecip.', 'Pcts Atraso', 'Total Pacotes']]; BI_DATA.routes.forEach(d => rtR.push([d.routeId, d.routeName, d.date, d.driverId, d.total, d.early, d.late, d.pkgsEarly || 0, d.pkgsLate || 0, d.packages])); XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rtR), 'Rotas Ofensoras');
    const cR = [['Cluster', 'Rotas', 'Total Paradas', 'On Time', 'Antecipações', 'Atrasos', '% On Time (pcts)', '% Antecip. (pcts)', '% Atraso (pcts)', 'Pcts On Time', 'Pcts Antecip.', 'Pcts Atraso', 'Antecip. 1ª Parada', 'Meta OnTime']]; BI_DATA.clusters.forEach(c => { cR.push([c.cluster, c.routes, c.total, c.ontime, c.early, c.late, c.pctOntime + '%', c.pctEarly + '%', c.pctLate + '%', c.pkgsOntime || 0, c.pkgsEarly || 0, c.pkgsLate || 0, c.firstEarlyCount, c.pctOntime >= T_ON ? 'Atingido' : 'Abaixo']); }); XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(cR), 'Por Cluster');
}

function exportarExcelBI() {
    if (!BI_DATA.raw || !BI_DATA.raw.length) return alert("Não existem dados carregados no Monitor de Pontualidade para exportar.");
    const hS = obterHubFiltroEstatisticas(), wb = XLSX.utils.book_new();
    adicionarAbasPontualidadeNoWorkbook(wb, hS);
    XLSX.writeFile(wb, `Ofensores_Pontualidade_Global_${hS}_${new Date().toISOString().split('T')[0]}.xlsx`);
}

// ==========================================
// MONITOR DE OCORRÊNCIAS (OC) - COM REVERTIDO E OBSERVAÇÕES EDITÁVEIS
// ==========================================
function obterEdicaoOcorrencia(routeId, stop, motivo, idx) {
    const chCompleta = gerarChaveOcorrencia(routeId, stop, motivo, idx);
    const chBase = gerarChaveRotaParadaMotivo(routeId, stop, motivo);

    if (controleOcorrencias[chCompleta]) return controleOcorrencias[chCompleta];
    if (controleOcorrencias[chBase]) return controleOcorrencias[chBase];

    const rotaPlanilha = rotasGlobais.find(r => String(r.id_rota).trim() === String(routeId).trim());
    if (rotaPlanilha && rotaPlanilha.revertido) {
        const rev = String(rotaPlanilha.revertido).toUpperCase().trim();
        if (rev === 'SIM' || rev === 'NÃO' || rev === 'NAO') {
            return { revertido: rev === 'NAO' ? 'NÃO' : rev, observacao: '' };
        }
    }
    return { revertido: 'NÃO', observacao: '' };
}

function gerarOC(rawRows) {
    if (!rawRows || rawRows.length === 0) {
        document.getElementById('oc-kpis').innerHTML = ''; document.getElementById('oc-tbl-pivot').querySelector('tbody').innerHTML = ''; document.getElementById('oc-tbl-details').querySelector('tbody').innerHTML = '';
        OC_DATA = { raw: [], rawWithEmpty: [], pivot: [], pivotWithEmpty: [], details: [], totalParadas: 0, totalOcorrencias: 0, pacotesLeftBehind: 0, topMotive: "" }; return;
    }

    const rows = rawRows.map(normalizeBPObj); const cm = colMapBI(rows[0]);
    const getVal = (r, keys) => { for (let k of keys) { if (r[k] !== undefined) return r[k]; } return ""; };

    let processedOccurrences = [];
    let processedAll = [];
    let totalP = 0;
    let leftBehindTotal = 0;
    let motiveCountOcc = {};
    let motiveCountAll = {};

    rows.forEach((r, idx) => {
        totalP++;

        let pEst = parseNumberBI(r[cm.estimated]);
        let pCol = parseNumberBI(r[cm.packages]);
        let mRaw = String(getVal(r, ['Motivo', 'motivo']) ?? '').trim();

        let pPrep = parseNumberBI(getVal(r, ['Pacotes não preparados/Não estão na agência', 'Pacotes não preparados', 'pacotes_nao_preparados']));
        let pEspaco = parseNumberBI(getVal(r, ['Pacotes não cabem no veículo', 'pacotes_nao_cabem']));
        let pMauEstado = parseNumberBI(getVal(r, ['Pacotes em mau estado', 'pacotes_mau_estado']));
        leftBehindTotal += (pPrep + pEspaco + pMauEstado);

        const isVazio = (!mRaw || mRaw.toLowerCase() === 'nan' || mRaw.toLowerCase() === 'none' || mRaw === '');
        const motivoOficial = isVazio ? '(vazio)' : mRaw;

        motiveCountAll[motivoOficial] = (motiveCountAll[motivoOficial] || 0) + pEst;

        const rId = String(r[cm.routeId] || '').trim();
        const stp = String(r[cm.stop] || '').trim();
        const edicao = obterEdicaoOcorrencia(rId, stp, motivoOficial, idx);
        const chaveBase = gerarChaveRotaParadaMotivo(rId, stp, motivoOficial);

        const itemRow = {
            idx: idx,
            chave: chaveBase,
            routeId: rId,
            routeName: String(r[cm.routeName] || '').trim(),
            driverId: String(r[cm.driverId] || '').trim(),
            stop: stp,
            motivo: motivoOficial,
            motivos: [motivoOficial],
            motivosStr: motivoOficial,
            est: pEst,
            col: pCol,
            revertido: edicao.revertido || 'NÃO',
            observacao: edicao.observacao || '',
            detalhes: String(getVal(r, ['Detalhes', 'detalhes', 'Evidência']) ?? '').trim()
        };

        processedAll.push(itemRow);

        if (!isVazio) {
            motiveCountOcc[motivoOficial] = (motiveCountOcc[motivoOficial] || 0) + pEst;
            processedOccurrences.push(itemRow);
        }
    });

    let pivotOcc = Object.keys(motiveCountOcc).map(k => ({ motivo: k, somaEst: motiveCountOcc[k] })).sort((a, b) => b.somaEst - a.somaEst);
    let pivotAll = Object.keys(motiveCountAll).map(k => ({ motivo: k, somaEst: motiveCountAll[k] })).sort((a, b) => {
        if (a.motivo === '(vazio)') return 1;
        if (b.motivo === '(vazio)') return -1;
        return b.somaEst - a.somaEst;
    });

    OC_DATA = {
        raw: processedOccurrences,
        rawWithEmpty: processedAll,
        pivot: pivotOcc,
        pivotWithEmpty: pivotAll,
        totalParadas: totalP,
        totalOcorrencias: processedOccurrences.length,
        pacotesLeftBehind: leftBehindTotal,
        topMotive: pivotOcc.length > 0 ? pivotOcc[0].motivo : "Nenhum registo"
    };

    renderOCKPIs(); renderOCPivot(); renderOCDetails();
}

function renderOCKPIs() {
    const pIns = OC_DATA.totalParadas > 0 ? ((OC_DATA.totalOcorrencias / OC_DATA.totalParadas) * 100).toFixed(1) : 0;
    const somaEstOcc = (OC_DATA.pivot || []).reduce((acc, i) => acc + i.somaEst, 0);
    const kpis = [
        { label: 'Paradas c/ Ocorrência', val: `${OC_DATA.totalOcorrencias.toLocaleString('pt-BR')} / ${OC_DATA.totalParadas.toLocaleString('pt-BR')}`, cls: 'alert' },
        { label: 'Pcts Estimados em Ocorrência', val: somaEstOcc.toLocaleString('pt-BR'), cls: 'late' },
        { label: 'Taxa de Ocorrência (Paradas)', val: `${pIns}%`, cls: 'early' },
        { label: 'Top Ofensor (Motivo)', val: OC_DATA.topMotive, cls: 'blue' }
    ];
    document.getElementById('oc-kpis').innerHTML = kpis.map(k => `<div class="bi-kpi ${k.cls}"><label>${k.label}</label><div class="val" style="font-size: ${k.label.includes('Top') ? '16px' : '22px'};">${k.val}</div></div>`).join('');
}

function renderOCPivot() {
    const tb = document.getElementById('oc-tbl-pivot').querySelector('tbody');
    const listaPivot = OC_DATA.pivotWithEmpty || [];

    let totG = 0;
    let html = listaPivot.map(i => {
        totG += i.somaEst;
        const sel = filtroMotivoAtualOC === i.motivo ? 'selected' : '';
        return `<tr class="${sel}" onclick="filtrarPorMotivoOC('${i.motivo.replace(/'/g, "\\'")}')"><td>${i.motivo}</td><td style="text-align: right; font-weight: 700;">${i.somaEst.toLocaleString('pt-BR')}</td></tr>`;
    }).join('');

    tb.innerHTML = html || `<tr><td colspan="2" style="text-align:center; color:#8a9abc; padding: 20px;">Nenhuma ocorrência encontrada.</td></tr>`;
    document.getElementById('oc-footer-pivot').innerText = `Total Geral: ${totG.toLocaleString('pt-BR')}`;
}

function renderOCDetails() {
    const term = document.getElementById('oc-search-details') ? document.getElementById('oc-search-details').value.toLowerCase() : '';
    let rows = OC_DATA.rawWithEmpty || [];

    if (filtroMotivoAtualOC) rows = rows.filter(r => r.motivo === filtroMotivoAtualOC);
    if (term) rows = rows.filter(r => r.routeId.toLowerCase().includes(term) || r.driverId.toLowerCase().includes(term) || r.stop.toLowerCase().includes(term) || r.detalhes.toLowerCase().includes(term) || (r.observacao || '').toLowerCase().includes(term));
    
    const tb = document.getElementById('oc-tbl-details').querySelector('tbody');
    const podeEditar = !currentUser || currentUser.cargo !== 'MONITOR GRAU 1';

    let html = rows.slice(0, 600).map(d => {
        let dT = d.detalhes && d.detalhes !== 'nan' && d.detalhes !== 'None' ? d.detalhes : '<span style="color:#cbd5e1">-</span>';
        const isSim = String(d.revertido || 'NÃO').toUpperCase() === 'SIM';
        const obsEscapada = String(d.observacao || '').replace(/"/g, '&quot;');

        return `
            <tr>
                <td style="font-family:monospace;font-size:11px;color:#4a5a7a">${d.routeId}</td>
                <td style="font-family:monospace;color:#0d1b3e;font-weight:700">${d.driverId}</td>
                <td>${d.stop}</td>
                <td style="color:${d.motivo === '(vazio)' ? '#64748b' : '#b91c1c'};font-weight:600">${d.motivo}</td>
                <td style="text-align: center; font-weight:700;">${d.est}</td>
                <td style="text-align: center;">${d.col}</td>
                <td style="text-align: center;">
                    <select class="oc-rev-select ${isSim ? 'sim' : 'nao'}" ${podeEditar ? '' : 'disabled'} onchange="aoMudarRevertidoOC(${d.idx}, this.value, this)">
                        <option value="NÃO" ${!isSim ? 'selected' : ''}>NÃO</option>
                        <option value="SIM" ${isSim ? 'selected' : ''}>SIM</option>
                    </select>
                </td>
                <td>
                    <input type="text" class="oc-obs-input" value="${obsEscapada}" placeholder="Adicionar observação..." ${podeEditar ? '' : 'disabled'} onchange="aoMudarObservacaoOC(${d.idx}, this.value)">
                </td>
                <td style="font-size:11px; color:#4a5a7a">${dT}</td>
            </tr>
        `;
    }).join('');

    tb.innerHTML = html || `<tr><td colspan="9" style="text-align:center; color:#8a9abc; padding: 20px;">Nenhuma ocorrência corresponde ao filtro.</td></tr>`;
    document.getElementById('oc-footer-details').innerText = rows.length > 600 ? `A mostrar 600 de ${rows.length} registos.` : `A mostrar ${rows.length} registos.`;
}

function aoMudarRevertidoOC(idx, novoValor, selectEl) {
    const valLimpo = String(novoValor || 'NÃO').toUpperCase().trim();
    if (selectEl) {
        selectEl.className = `oc-rev-select ${valLimpo === 'SIM' ? 'sim' : 'nao'}`;
    }

    const item = (OC_DATA.rawWithEmpty || []).find(x => x.idx === idx);
    if (!item) return;

    item.revertido = valLimpo;
    (OC_DATA.raw || []).forEach(x => { if (x.idx === idx || x.chave === item.chave) x.revertido = valLimpo; });
    (OC_DATA.rawWithEmpty || []).forEach(x => { if (x.chave === item.chave) x.revertido = valLimpo; });

    controleOcorrencias[item.chave] = {
        id_rota: item.routeId,
        revertido: valLimpo,
        observacao: item.observacao || ''
    };

    const baseFiltrada = filtrarBaseRelatorio('filtro-data-est', 'filtro-hub-est', 'data-ini-est', 'data-fim-est', 'container-periodo-est');
    gerarGeralOperacao(baseFiltrada);

    fetch(`${API_BASE_URL}/salvar_edicao_ocorrencia`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            chave: item.chave,
            id_rota: item.routeId,
            revertido: valLimpo,
            observacao: item.observacao || ''
        })
    }).catch(e => console.error(e));
}

function aoMudarObservacaoOC(idx, novaObs) {
    const obsLimpa = String(novaObs || '').trim();
    const item = (OC_DATA.rawWithEmpty || []).find(x => x.idx === idx);
    if (!item) return;

    item.observacao = obsLimpa;
    (OC_DATA.raw || []).forEach(x => { if (x.idx === idx || x.chave === item.chave) x.observacao = obsLimpa; });
    (OC_DATA.rawWithEmpty || []).forEach(x => { if (x.chave === item.chave) x.observacao = obsLimpa; });

    controleOcorrencias[item.chave] = {
        id_rota: item.routeId,
        revertido: item.revertido || 'NÃO',
        observacao: obsLimpa
    };

    fetch(`${API_BASE_URL}/salvar_edicao_ocorrencia`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            chave: item.chave,
            id_rota: item.routeId,
            revertido: item.revertido || 'NÃO',
            observacao: obsLimpa
        })
    }).catch(e => console.error(e));
}

function filtrarPorMotivoOC(motivo) {
    if (filtroMotivoAtualOC === motivo) limparFiltroMotivoOC();
    else { filtroMotivoAtualOC = motivo; document.getElementById('btn-limpar-motivo-oc').style.display = 'block'; document.getElementById('oc-details-title').innerText = `📋 Detalhes para: ${motivo}`; renderOCPivot(); renderOCDetails(); }
}

function limparFiltroMotivoOC() {
    filtroMotivoAtualOC = "";
    if(document.getElementById('btn-limpar-motivo-oc')) document.getElementById('btn-limpar-motivo-oc').style.display = 'none';
    if(document.getElementById('oc-details-title')) document.getElementById('oc-details-title').innerText = "📋 Todas as Ocorrências";
    if(OC_DATA.rawWithEmpty && OC_DATA.rawWithEmpty.length > 0) { renderOCPivot(); renderOCDetails(); }
}

function exportarExcelOC() {
    if (!OC_DATA.rawWithEmpty || OC_DATA.rawWithEmpty.length === 0) return alert("Não existem dados de ocorrências para exportar.");
    const hS = obterHubFiltroEstatisticas(); const wb = XLSX.utils.book_new();
    const listaPivot = OC_DATA.pivotWithEmpty || [];
    const listaDetalhes = OC_DATA.rawWithEmpty || [];

    const pR = [['Motivos', 'Soma de Pacotes estimados']];
    let somaTotal = 0;
    listaPivot.forEach(p => { pR.push([p.motivo, p.somaEst]); somaTotal += p.somaEst; });
    pR.push(['Total Geral', somaTotal]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(pR), 'Tabela Geral Ocorrências');

    const dR = [['ID Rota', 'ID Motorista', 'Parada', 'Motivo', 'Pcts Est.', 'Pcts Col.', 'Revertido', 'Observações', 'Detalhes / Evidência']];
    listaDetalhes.forEach(r => dR.push([r.routeId, r.driverId, r.stop, r.motivo, r.est, r.col, r.revertido || 'NÃO', r.observacao || '', r.detalhes]));
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(dR), 'Todas as Ocorrências');
    XLSX.writeFile(wb, `Monitor_Ocorrencias_${hS}_${new Date().toISOString().split('T')[0]}.xlsx`);
}

function exportarEstratificacaoOC() {
    if (!OC_DATA.rawWithEmpty || OC_DATA.rawWithEmpty.length === 0) return alert("Não existem dados filtrados para exportar.");
    const tM = document.getElementById('oc-search-details') ? document.getElementById('oc-search-details').value.toLowerCase() : '';
    let rs = OC_DATA.rawWithEmpty || [];

    if (filtroMotivoAtualOC) rs = rs.filter(r => r.motivo === filtroMotivoAtualOC);
    if (tM) rs = rs.filter(r => r.routeId.toLowerCase().includes(tM) || r.driverId.toLowerCase().includes(tM) || r.stop.toLowerCase().includes(tM) || r.detalhes.toLowerCase().includes(tM) || (r.observacao || '').toLowerCase().includes(tM));
    const hS = obterHubFiltroEstatisticas(), wb = XLSX.utils.book_new();
    const dR = [['ID Rota', 'ID Motorista', 'Parada', 'Motivo', 'Pcts Est.', 'Pcts Col.', 'Revertido', 'Observações', 'Detalhes / Evidência']];
    rs.forEach(r => dR.push([r.routeId, r.driverId, r.stop, r.motivo, r.est, r.col, r.revertido || 'NÃO', r.observacao || '', r.detalhes]));
    let sN = filtroMotivoAtualOC ? filtroMotivoAtualOC.substring(0,25) : "Todas_Ocorrencias"; XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(dR), sN.replace(/[\\/*?:[\]]/g, ''));
    XLSX.writeFile(wb, `Estratificacao_OC_${hS}_${new Date().toISOString().split('T')[0]}.xlsx`);
}

// ==========================================
// SUB-ABA 3: GERAL DA OPERAÇÃO (LAYOUT COMPACTO E SINCRONIZADO)
// ==========================================
function gerarGeralOperacao(rawRows) {
    if (!rawRows || rawRows.length === 0) {
        GO_DATA = { totalColetados: 0, totalEstimados: 0, totalOfensoresPcts: 0, totalRevertidosPcts: 0, qtdRotasRevertidas: 0, qtdRotasComOcorrencia: 0, motivosAgrupados: [] };
        renderizarGeralOperacao();
        return;
    }

    const totalColetados = BI_DATA.pkgsTotal || 0;
    const pctsForaJanela = (BI_DATA.pkgsEarly || 0) + (BI_DATA.pkgsLate || 0);

    const mapaMotivos = {};
    const rotasOfensorasUnicas = new Map();
    let totalRevertidosPcts = 0;
    const rotasRevertidasSet = new Set();

    (OC_DATA.raw || []).forEach(occ => {
        const rId = occ.routeId || 'SEM_ID';
        const isOccRev = String(occ.revertido || 'NÃO').toUpperCase() === 'SIM';

        if (!rotasOfensorasUnicas.has(rId)) {
            rotasOfensorasUnicas.set(rId, {
                routeId: rId,
                routeName: occ.routeName || rId,
                driverId: occ.driverId || '-',
                est: 0,
                col: 0
            });
        }
        const rUnica = rotasOfensorasUnicas.get(rId);
        rUnica.est += occ.est || 0;
        rUnica.col += occ.col || 0;

        if (isOccRev) {
            totalRevertidosPcts += (occ.est || 0);
            rotasRevertidasSet.add(rId);
        }

        const motivo = occ.motivo;
        if (!mapaMotivos[motivo]) {
            mapaMotivos[motivo] = {
                motivo: motivo,
                somaEstTotal: 0,
                qtdParadas: 0,
                rotasMap: {}
            };
        }
        mapaMotivos[motivo].somaEstTotal += occ.est || 0;
        mapaMotivos[motivo].qtdParadas += 1;

        if (!mapaMotivos[motivo].rotasMap[rId]) {
            mapaMotivos[motivo].rotasMap[rId] = {
                routeId: rId,
                routeName: occ.routeName || rId,
                driverId: occ.driverId || '-',
                est: 0,
                col: 0,
                paradas: 0,
                algumaRevertida: false
            };
        }
        mapaMotivos[motivo].rotasMap[rId].est += occ.est || 0;
        mapaMotivos[motivo].rotasMap[rId].col += occ.col || 0;
        mapaMotivos[motivo].rotasMap[rId].paradas += 1;
        if (isOccRev) {
            mapaMotivos[motivo].rotasMap[rId].algumaRevertida = true;
        }
    });

    let totalOfensoresOcorrenciaPcts = 0;
    rotasOfensorasUnicas.forEach(rt => {
        totalOfensoresOcorrenciaPcts += rt.est;
    });

    const motivosAgrupados = Object.values(mapaMotivos).map(m => {
        const listaRotas = Object.values(m.rotasMap).map(r => {
            return { ...r, revertido: r.algumaRevertida ? 'SIM' : 'NÃO' };
        }).sort((a, b) => b.est - a.est);

        return {
            motivo: m.motivo,
            somaEstTotal: m.somaEstTotal,
            qtdParadas: m.qtdParadas,
            rotas: listaRotas
        };
    }).sort((a, b) => b.somaEstTotal - a.somaEstTotal);

    GO_DATA = {
        totalColetados: totalColetados,
        totalOfensoresPcts: totalOfensoresOcorrenciaPcts,
        totalOfensoresPontualidade: pctsForaJanela,
        totalRevertidosPcts: totalRevertidosPcts,
        qtdRotasRevertidas: rotasRevertidasSet.size,
        qtdRotasComOcorrencia: rotasOfensorasUnicas.size,
        motivosAgrupados: motivosAgrupados
    };

    renderizarGeralOperacao();
}

function renderizarGeralOperacao() {
    const kpiContainer = document.getElementById('go-kpis');
    const chartContainer = document.getElementById('go-ontime-chart-panel');
    const occGrid = document.getElementById('go-ocorrencias-grid');
    if (!kpiContainer || !chartContainer || !occGrid) return;

    kpiContainer.innerHTML = `
        <div class="go-kpi-card coletados">
            <div class="go-kpi-info">
                <label>📦 Total de Pacotes Coletados</label>
                <div class="go-val" style="color: #0284c7;">${(GO_DATA.totalColetados || 0).toLocaleString('pt-BR')}</div>
                <div class="go-sub">Volume total coletado no filtro selecionado</div>
            </div>
            <div class="go-kpi-icon" style="background: #e0f2fe; color: #0284c7;">📦</div>
        </div>

        <div class="go-kpi-card ofensores">
            <div class="go-kpi-info">
                <label>🚨 Total de Pacotes Ofensores</label>
                <div class="go-val" style="color: #dc2626;">${(GO_DATA.totalOfensoresPcts || 0).toLocaleString('pt-BR')}</div>
                <div class="go-sub">${GO_DATA.qtdRotasComOcorrencia} rotas em ocorrência | ${(GO_DATA.totalOfensoresPontualidade || 0).toLocaleString('pt-BR')} pcts fora da janela</div>
            </div>
            <div class="go-kpi-icon" style="background: #fee2e2; color: #dc2626;">⚠️</div>
        </div>

        <div class="go-kpi-card revertidos">
            <div class="go-kpi-info">
                <label>🔄 Total de Revertidos</label>
                <div class="go-val" style="color: #16a34a;">${(GO_DATA.totalRevertidosPcts || 0).toLocaleString('pt-BR')}</div>
                <div class="go-sub">${GO_DATA.qtdRotasRevertidas} rota(s) revertida(s) na operação</div>
            </div>
            <div class="go-kpi-icon" style="background: #dcfce7; color: #16a34a;">✅</div>
        </div>
    `;

    const pT = BI_DATA.pkgsTotal || 0;
    const pO = BI_DATA.pkgsOntime || 0;
    const pE = BI_DATA.pkgsEarly || 0;
    const pL = BI_DATA.pkgsLate || 0;

    const pctO = pT > 0 ? +(pO / pT * 100).toFixed(1) : 0;
    const pctE = pT > 0 ? +(pE / pT * 100).toFixed(1) : 0;
    const pctL = pT > 0 ? +(pL / pT * 100).toFixed(1) : 0;

    const hS = obterHubFiltroEstatisticas();
    const T_ON = obterMetaAtiva(hS);
    const okMeta = pctO >= T_ON;
    const ymLabel = obterAnoMesAtivoFiltro().split('-').reverse().join('/');

    const degOntime = (pctO * 3.6).toFixed(1);
    const degEarly = ((pctO + pctE) * 3.6).toFixed(1);

    chartContainer.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
            <div>
                <h3 style="margin: 0; color: #0d1b3e; font-size: 14.5px;">📈 Performance On-Time Geral (${hS} · Ref: ${ymLabel})</h3>
                <p style="margin: 2px 0 0 0; font-size: 11px; color: #64748b;">Meta estipulada: <b>${T_ON}%</b></p>
            </div>
            <span style="padding: 4px 10px; border-radius: 16px; font-size: 11px; font-weight: 800; background: ${okMeta ? '#dcfce7' : '#fee2e2'}; color: ${okMeta ? '#15803d' : '#b91c1c'};">
                ${okMeta ? '✅ META ATINGIDA' : '⚠️ ABAIXO DA META'} (${pctO}%)
            </span>
        </div>

        <div class="go-donut-box">
            <div class="go-donut" style="background: conic-gradient(#22c55e 0deg ${degOntime}deg, #f97316 ${degOntime}deg ${degEarly}deg, #ef4444 ${degEarly}deg 360deg);">
                <div class="go-donut-hole">
                    <span class="pct" style="color: ${okMeta ? '#15803d' : '#b91c1c'};">${pctO}%</span>
                    <span class="lbl">On Time</span>
                </div>
            </div>
            <div style="display: flex; flex-direction: column; gap: 10px; font-size: 12.5px;">
                <div style="display: flex; align-items: center; gap: 8px;"><span style="width:12px;height:12px;border-radius:3px;background:#22c55e;display:inline-block;"></span> <span><b>On Time:</b> ${pO.toLocaleString('pt-BR')} pcts (${pctO}%)</span></div>
                <div style="display: flex; align-items: center; gap: 8px;"><span style="width:12px;height:12px;border-radius:3px;background:#f97316;display:inline-block;"></span> <span><b>Antecipados:</b> ${pE.toLocaleString('pt-BR')} pcts (${pctE}%)</span></div>
                <div style="display: flex; align-items: center; gap: 8px;"><span style="width:12px;height:12px;border-radius:3px;background:#ef4444;display:inline-block;"></span> <span><b>Atrasados:</b> ${pL.toLocaleString('pt-BR')} pcts (${pctL}%)</span></div>
            </div>
        </div>
    `;

    const corteSelect = document.getElementById('go-filtro-corte-pcts');
    const limitePacotes = corteSelect ? parseInt(corteSelect.value) : 100;

    if (!GO_DATA.motivosAgrupados || GO_DATA.motivosAgrupados.length === 0) {
        occGrid.innerHTML = `<div style="grid-column: 1 / -1; padding: 30px; text-align: center; color: #8a9abc; font-size: 13px;">Nenhuma ocorrência registada para o filtro atual.</div>`;
        return;
    }

    occGrid.innerHTML = GO_DATA.motivosAgrupados.map(grupo => {
        const rotasFiltradas = grupo.rotas.filter(r => r.est >= limitePacotes);

        const tabelaHTML = rotasFiltradas.length > 0 ? `
            <table class="go-pivot-table">
                <thead>
                    <tr>
                        <th style="text-align: left;">Rota</th>
                        <th style="text-align: right;">Pacotes</th>
                        <th style="text-align: center;">Revertido</th>
                    </tr>
                </thead>
                <tbody>
                    ${rotasFiltradas.map(r => {
                        const isRev = r.revertido === 'SIM';
                        return `
                            <tr class="${isRev ? 'row-revertido' : ''}">
                                <td>${r.routeName || r.routeId}</td>
                                <td style="text-align: right; color: #b91c1c; font-weight: 800;">${r.est.toLocaleString('pt-BR')}</td>
                                <td style="text-align: center;">
                                    <span class="go-rev-badge ${isRev ? 'sim' : 'nao'}">${isRev ? 'SIM' : 'NÃO'}</span>
                                </td>
                            </tr>
                        `;
                    }).join('')}
                </tbody>
            </table>
        ` : `<div style="padding: 14px; text-align: center; color: #94a3b8; font-size: 11.5px; font-style: italic;">Nenhuma rota com mais de ${limitePacotes} pacotes neste motivo.</div>`;

        return `
            <div class="go-occ-card">
                <div class="go-occ-header">
                    <div class="go-occ-title">📌 ${grupo.motivo}</div>
                    <div class="go-occ-badge">${grupo.somaEstTotal.toLocaleString('pt-BR')} pcts</div>
                </div>
                <div class="go-occ-body">
                    ${tabelaHTML}
                </div>
            </div>
        `;
    }).join('');
}

function exportarExcelGeralOperacao() {
    if (!BI_DATA.raw || BI_DATA.raw.length === 0) return alert("Não existem dados carregados para exportar.");
    const hS = obterHubFiltroEstatisticas();
    const wb = XLSX.utils.book_new();
    adicionarAbasGeralOperacaoNoWorkbook(wb);
    XLSX.writeFile(wb, `Geral_da_Operacao_${hS}_${new Date().toISOString().split('T')[0]}.xlsx`);
}

function adicionarAbasGeralOperacaoNoWorkbook(wb) {
    const kpiSheet = [
        ['Indicador Geral da Operação', 'Valor'],
        ['Total de Pacotes Coletados', GO_DATA.totalColetados || 0],
        ['Total de Pacotes Ofensores', GO_DATA.totalOfensoresPcts || 0],
        ['Total de Pacotes Revertidos', GO_DATA.totalRevertidosPcts || 0],
        ['Rotas com Ocorrência', GO_DATA.qtdRotasComOcorrencia || 0],
        ['Rotas Revertidas', GO_DATA.qtdRotasRevertidas || 0]
    ];
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(kpiSheet), 'Geral Operação KPIs');

    const ofensorasSheet = [['Motivo da Ocorrência', 'Rota', 'ID Rota', 'Pacotes Estimados', 'Acima de 100 Pcts?', 'Revertido']];
    (GO_DATA.motivosAgrupados || []).forEach(g => {
        g.rotas.forEach(r => {
            ofensorasSheet.push([g.motivo, r.routeName || r.routeId, r.routeId, r.est, r.est >= 100 ? 'SIM' : 'NÃO', r.revertido]);
        });
    });
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(ofensorasSheet), 'Resumo Geral Ocorrências');
}

function exportarExcelFiltroCompleto() {
    if ((!BI_DATA.raw || BI_DATA.raw.length === 0) && (!OC_DATA.rawWithEmpty || OC_DATA.rawWithEmpty.length === 0)) {
        return alert("Não existem dados carregados para o filtro selecionado.");
    }
    const hS = obterHubFiltroEstatisticas();
    const wb = XLSX.utils.book_new();

    adicionarAbasGeralOperacaoNoWorkbook(wb);
    adicionarAbasPontualidadeNoWorkbook(wb, hS);

    const pR = [['Motivos', 'Soma de Pacotes estimados']];
    let totG = 0;
    (OC_DATA.pivotWithEmpty || []).forEach(p => { pR.push([p.motivo, p.somaEst]); totG += p.somaEst; });
    pR.push(['Total Geral', totG]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(pR), 'Tabela Geral Ocorrências');

    const dR = [['ID Rota', 'ID Motorista', 'Parada', 'Motivo', 'Pcts Est.', 'Pcts Col.', 'Revertido', 'Observações', 'Detalhes / Evidência']];
    (OC_DATA.rawWithEmpty || []).forEach(r => dR.push([r.routeId, r.driverId, r.stop, r.motivo, r.est, r.col, r.revertido || 'NÃO', r.observacao || '', r.detalhes]));
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(dR), 'Todas as Ocorrências');

    XLSX.writeFile(wb, `Relatorio_Completo_Unificado_${hS}_${new Date().toISOString().split('T')[0]}.xlsx`);
}

// ==========================================
// MENUS, MODAIS E EXPORTAÇÃO (PLANILHA)
// ==========================================
document.addEventListener('click', function(e) {
    if (!e.target.closest('.color-dropdown')) document.querySelectorAll(".color-menu").forEach(m => m.classList.remove('show'));
    const sb = document.getElementById("sidebar"), mI = document.querySelector(".menu-icon");
    if (sb && sb.classList.contains("aberta") && !sb.contains(e.target) && !mI.contains(e.target)) { sb.classList.remove("aberta"); document.getElementById("main-content").classList.remove("empurrado"); }
    const uM = document.getElementById('userMenu'); if (uM && uM.classList.contains('show') && !e.target.closest('.user-profile')) uM.classList.remove('show');
});
function mudarAba(id, btn){ document.querySelectorAll('.conteudo-aba, .sidebar-btn').forEach(e => e.classList.remove('ativo')); document.getElementById(id).classList.add('ativo'); if (btn) btn.classList.add('ativo'); if(id === 'aba-monitoramento' && gridApi) setTimeout(() => { restaurarEstadoColunas(); atualizarContadorRotas(); }, 50); }
function navegarPelosCards(idAba, idBotaoSidebar) { mudarAba(idAba, document.getElementById(idBotaoSidebar)); const sb = document.getElementById("sidebar"); if (sb && sb.classList.contains("aberta")) { sb.classList.remove("aberta"); document.getElementById("main-content").classList.remove("empurrado"); } }
function mudarSubAba(id, btn){ const aP = btn ? btn.closest('.conteudo-aba') : null; if (aP) { aP.querySelectorAll('.sub-conteudo, .sub-tab-btn').forEach(e => e.classList.remove('ativo')); } else { document.querySelectorAll('.sub-conteudo, .sub-tab-btn').forEach(e => e.classList.remove('ativo')); } document.getElementById(id).classList.add('ativo'); if (btn) btn.classList.add('ativo'); }
function toggleMenu(){ document.getElementById('sidebar').classList.toggle('aberta'); document.getElementById('main-content').classList.toggle('empurrado'); }
function abrirModalExportacao(){ const lc = document.getElementById('colunas-export-list'); lc.innerHTML = ''; gridApi.getColumns().forEach(c => { let l = document.createElement('label'); l.style.cssText = 'display:flex; align-items:center; gap:8px; font-size:13px; font-weight:600; cursor:pointer; color:#334155;'; l.innerHTML = `<input type="checkbox" value="${c.getColId()}" checked style="width:16px; height:16px; accent-color:#1a237e;"> ${c.getColDef().headerName}`; lc.appendChild(l); }); document.getElementById('exportModal').classList.add('show'); }
function fecharModalExportacao() { document.getElementById('exportModal').classList.remove('show'); }
function marcarTodasColunasExport(est) { Array.from(document.querySelectorAll('#colunas-export-list input:checkbox')).forEach(cb => cb.checked = est); }
function confirmarExportacao(){ const ks = Array.from(document.querySelectorAll('#colunas-export-list input:checked')).map(i=>i.value); if(ks.length > 0) gridApi.exportDataAsCsv({ columnKeys: ks, fileName: `Monitoramento_${new Date().toISOString().split('T')[0]}.csv` }); else alert("Selecione pelo menos uma coluna!"); fecharModalExportacao(); }

// ==========================================
// ABA DE ESCALA
// ==========================================
function inicializarEscala(){
    let d = document.getElementById('data-escala').value.split('-').reverse().join('/'), hS = document.getElementById('filtro-hub-escala') ? document.getElementById('filtro-hub-escala').value : "TODOS";
    const pR = document.getElementById('escala-resumo-painel'), bS = document.getElementById('btn-salvar-escala'), av = document.getElementById('aviso-salvamento');
    clusterData = {}; monitorAssignments = {};
    if (hS === "TODOS") {
        document.getElementById('escala-body').innerHTML = `<div style="width: 100%; background: white; border: 1px dashed #cbd5e1; border-radius: 10px; padding: 45px 20px; text-align: center; box-shadow: 0 2px 6px rgba(0,0,0,0.02);"><div style="font-size: 32px; margin-bottom: 10px;">📍</div><h3 style="color: #1a237e; margin: 0 0 6px 0; font-size: 18px;">Selecione um HUB para editar a escala</h3><p style="color: #64748b; margin: 0; font-size: 14px;">Utilize o filtro <b>"Filtrar HUB"</b> acima para exibir os clusters e o resumo de monitores.</p></div>`;
        if (pR) pR.style.display = 'none'; if (bS) bS.disabled = true; if (av) av.innerHTML = "Selecione um HUB específico para liberar a edição."; return;
    }
    if (bS && (!currentUser || currentUser.cargo !== 'MONITOR GRAU 1')) bS.disabled = false; if (av) av.innerHTML = "Preencha os monitores e clique em Salvar Escala.";
    rotasGlobais.filter(r => r.data.includes(d)).forEach(r => {
        let hR = r.nome_rota ? r.nome_rota.split('_')[0].toUpperCase() : "";
        if (hS === "TODOS" || hR === hS || hR.includes(hS)) { if(r.cluster && String(r.cluster).toUpperCase() !== 'SEM CLUSTER') { let cK = String(r.cluster).toUpperCase().trim(); clusterData[cK] = (clusterData[cK] || 0) + 1; if(r.monitor) monitorAssignments[cK] = String(r.monitor).toUpperCase().trim(); } }
    });
    const dS = (currentUser && currentUser.cargo === 'MONITOR GRAU 1') ? "disabled title='Somente Leitura'" : "";
    if(Object.keys(clusterData).length === 0) { document.getElementById('escala-body').innerHTML = '<h3 style="color:#d32f2f; width:100%; text-align:center;">Não existem rotas para esta data ou HUB selecionado.</h3>'; if (pR) pR.style.display = 'none'; return; }
    document.getElementById('escala-body').innerHTML = Object.keys(clusterData).sort().map(c => `<div class="cluster-group"><div style="font-size:13px; color:#1a237e;"><b>${c}</b> <span style="color:#666; font-size:11px;">(${clusterData[c]} rotas)</span></div><input type="text" style="text-transform: uppercase;" placeholder="NOME DO MONITOR" value="${(monitorAssignments[c]||'').toUpperCase()}" oninput="this.value = this.value.toUpperCase(); aoDigitarMonitor('${c}', this.value)" ${dS}></div>`).join('');
    renderizarResumoEscala();
}
function aoDigitarMonitor(c, m) { monitorAssignments[c] = String(m || "").toUpperCase().trim(); if (document.getElementById('aviso-salvamento')) document.getElementById('aviso-salvamento').innerHTML = "<span style='color:#d97706;'>⚠️ Alterações pendentes. Clique em Salvar Escala!</span>"; renderizarResumoEscala(); }
function renderizarResumoEscala() {
    const pR = document.getElementById('escala-resumo-painel'); if (!pR) return; const hS = document.getElementById('filtro-hub-escala') ? document.getElementById('filtro-hub-escala').value : "TODOS", cls = Object.keys(clusterData).sort();
    if (hS === "TODOS" || cls.length === 0) { pR.style.display = 'none'; return; } pR.style.display = 'block';
    let gM = {}; let tR = 0;
    cls.forEach(c => { let q = clusterData[c] || 0; tR += q; let nM = String(monitorAssignments[c] || "").toUpperCase().trim(); let k = nM ? nM : "SEM MONITOR"; if (!gM[k]) gM[k] = { nome: k, itens: [], tM: 0 }; gM[k].itens.push({ c: c, r: q }); gM[k].tM += q; });
    const pal = ["#ff66cc", "#cc66ff", "#e5b8e8", "#10b981", "#00b0f0", "#facc15", "#fb923c", "#a3e635"];
    let ord = Object.keys(gM).sort((a, b) => { if (a === "SEM MONITOR") return 1; if (b === "SEM MONITOR") return -1; return a.localeCompare(b); });
    let ht = ""; let cI = 0;
    ord.forEach(k => {
        let g = gM[k]; let cF = (k === "SEM MONITOR") ? "#f1f5f9" : pal[cI % pal.length]; if (k !== "SEM MONITOR") cI++; let qC = g.itens.length;
        g.itens.forEach((i, idx) => { ht += `<tr style="background-color: ${cF};"><td>${g.nome}</td><td>${i.c}</td><td>${i.r}</td>`; if (idx === 0) ht += `<td rowspan="${qC}" style="vertical-align: middle; font-size: 16px;">${g.tM}</td>`; ht += `</tr>`; });
    });
    pR.innerHTML = `<div class="escala-resumo-header">📊 RESUMO ESCALA — ${hS}</div><table class="escala-excel-table"><thead><tr><th>Monitor</th><th>Cluster</th><th>Rotas</th><th>Total</th></tr></thead><tbody>${ht}<tr class="escala-excel-total-row"><td colspan="3">TOTAL ROTAS</td><td>${tR}</td></tr></tbody></table>`;
}
function salvarEscalaManual() {
    if (currentUser && currentUser.cargo === 'MONITOR GRAU 1') return;
    let dB = document.getElementById('data-escala').value.split('-').reverse().join('/'), hS = document.getElementById('filtro-hub-escala') ? document.getElementById('filtro-hub-escala').value : "TODOS", av = document.getElementById('aviso-salvamento'), btn = document.getElementById('btn-salvar-escala'), aL = {};
    Object.keys(clusterData).forEach(c => { aL[c] = String(monitorAssignments[c] || "").toUpperCase().trim(); monitorAssignments[c] = aL[c]; });
    if (av) av.innerHTML = "<span style='color:#f57c00;'>⏳ A guardar escala no banco...</span>"; if (btn) btn.disabled = true;
    let up = [];
    rotasGlobais.forEach(r => { let hR = r.nome_rota ? r.nome_rota.split('_')[0].toUpperCase() : ""; let bH = (hS === "TODOS" || hR === hS || hR.includes(hS)); let cR = String(r.cluster || "").toUpperCase().trim(); if (bH && String(r.data).includes(dB) && cR in aL) r.monitor = aL[cR]; });
    if (gridApi) { gridApi.forEachNode(n => { let hR = n.data.nome_rota ? n.data.nome_rota.split('_')[0].toUpperCase() : ""; let bH = (hS === "TODOS" || hR === hS || hR.includes(hS)); let cR = String(n.data.cluster || "").toUpperCase().trim(); if (bH && String(n.data.data).includes(dB) && cR in aL) { n.data.monitor = aL[cR]; up.push(n.data); } }); if (up.length > 0) gridApi.applyTransaction({ update: up }); }
    fetch(`${API_BASE_URL}/salvar_escala`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data: dB, hub: hS, atribuicoes: aL }) })
    .then(r => r.json()).then(res => { if (btn) btn.disabled = false; if (res.sucesso) { if (av) av.innerHTML = "<span style='color:#15803d;'>✔️ Escala salva e padronizada com sucesso!</span>"; renderizarResumoEscala(); } else { if (av) av.innerHTML = `<span style='color:#d32f2f;'>❌ Erro: ${res.erro || 'Falha ao guardar'}</span>`; } })
    .catch(() => { if (btn) btn.disabled = false; if (av) av.innerHTML = "<span style='color:#d32f2f;'>❌ Erro de conexão com o servidor!</span>"; });
}

// ==========================================
// PAINEL DO ROBÔ (TICKETS)
// ==========================================
function mudarTipoTicket() {
    const t = document.getElementById('config-tipo').value, a = document.getElementById('config-acao').value; 
    document.getElementById('painel-programacao').style.display = (t === 'rotina') ? 'block' : 'none'; document.getElementById('painel-opcoes-relatorio').style.display = (a === 'relatorio' && t === 'avulso') ? 'block' : 'none';
}
function toggleDia(d) {
    const ck = document.querySelector(`.chk-dia[value="${d}"]`), ct = document.getElementById(`slots-${d}`);
    if(ck.checked) { ct.style.opacity = '1'; ct.style.pointerEvents = 'auto'; } else { ct.style.opacity = '0.5'; ct.style.pointerEvents = 'none'; }
}
function adicionarSlot(d) {
    const ct = document.getElementById(`slots-${d}`), nS = document.createElement('div'); nS.className = 'slot-row';
    nS.innerHTML = `<span class="slot-label">Intervalo:</span><input type="number" class="int-val" value="60" min="1"><span class="slot-label">min</span><span class="slot-label" style="margin-left:10px;">De:</span><input type="time" class="ini-val" value="19:00"><span class="slot-label" style="margin-left:10px;">Até:</span><input type="time" class="fim-val" value="21:00"><button onclick="this.parentElement.remove()" style="background: none; border: none; color: #ef4444; font-size: 14px; cursor: pointer; margin-left: auto;">❌</button>`;
    ct.appendChild(nS);
}
function carregarTickets(){ 
    fetch(`${API_BASE_URL}/tickets`).then(r=>r.json()).then(d=>{ 
        ticketsAtivos = d; 
        document.getElementById('lista-tickets-container').innerHTML = d.map(t=>`<div style="background:#fff;padding:12px;border-left:4px solid #ffd700;margin-bottom:10px;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,0.1);display:flex;justify-content:space-between; align-items:center;"><div style="flex: 1;"><b style="color:#1e293b;font-size:14px">${t.hub}</b><br><span style="font-size:11px;color:#64748b">${t.acao.toUpperCase()} | Última: ${t.ultima_coleta}</span></div><div id="count-${t.hub}-${t.acao}" style="text-align:center; min-width: 140px; margin: 0 10px;"></div><div style="display:flex; gap:8px; align-items:center;"><button onclick="editarTicket('${t.hub}', '${t.acao}')" style="background:#e0f2fe;color:#0284c7;border:1px solid #bae6fd;border-radius:4px;cursor:pointer;font-weight:bold;padding:4px 8px">✏️ Editar</button><button onclick="removerTicket('${t.hub}', '${t.acao}')" style="background:#fee2e2;color:#ef4444;border:1px solid #fecaca;border-radius:4px;cursor:pointer;font-weight:bold;padding:4px 8px">🗑️ Excluir</button></div></div>`).join('') || '<div style="color:#888;font-style:italic">Nenhum ticket na fila.</div>'; 
        atualizarCountdowns(); 
    }); 
}
function atualizarCountdowns() {
    const ag = new Date(), ds = ["Domingo", "Segunda", "Terca", "Quarta", "Quinta", "Sexta", "Sabado"], hs = ds[ag.getDay()], hA = ag.getHours() * 60 + ag.getMinutes(); 
    ticketsAtivos.forEach(t => {
        const el = document.getElementById(`count-${t.hub}-${t.acao}`); if (!el) return;
        if (t.tipo === 'avulso') { el.innerHTML = '<span style="color:#d97706;font-weight:bold;font-size:12px;">🚀 Fila Imediata</span>'; return; }
        let rs = {}; try { rs = typeof t.regras === 'string' ? JSON.parse(t.regras) : t.regras; } catch(e){}
        if (!rs[hs] || rs[hs].length === 0) { el.innerHTML = '<span style="color:#64748b;font-weight:bold;font-size:12px;">💤 Pausado Hoje</span>'; return; }
        let sA = null;
        for (let s of rs[hs]) { let [hI, mI] = s.ini.split(':').map(Number), [hF, mF] = s.fim.split(':').map(Number), mIn = hI * 60 + mI, mFm = hF * 60 + mF; if (hA >= mIn && hA <= mFm) { sA = s; break; } }
        if (!sA) { el.innerHTML = '<span style="color:#64748b;font-weight:bold;font-size:12px;">⏳ Fora do Horário</span>'; return; }
        if (!t.ultima_coleta || t.ultima_coleta === 'Aguardando' || t.ultima_coleta === 'None') { el.innerHTML = '<span style="color:#10b981;font-weight:bold;font-size:12px;">⚡ Na fila de Execução</span>'; return; }
        let ps = t.ultima_coleta.split(/[- :]/);
        if (ps.length === 6) {
            let u = new Date(ps[0], ps[1]-1, ps[2], ps[3], ps[4], ps[5]), pr = new Date(u.getTime() + sA.intervalo * 60000), dM = pr - ag;
            if (dM <= 0) el.innerHTML = '<span style="color:#10b981;font-weight:bold;font-size:12px;">⚡ Na fila de Execução</span>';
            else { let dm = Math.floor(dM / 60000), dsc = Math.floor((dM % 60000) / 1000); el.innerHTML = `<span style="color:#2563eb;font-weight:bold;background:#eff6ff;padding:4px 8px;border-radius:4px;border:1px solid #bfdbfe;font-size:12px;">⏱️ Próxima em ${String(dm).padStart(2, '0')}:${String(dsc).padStart(2, '0')}</span>`; }
        } else el.innerHTML = '<span style="color:#64748b;font-size:12px;">Calculando...</span>';
    });
}
function editarTicket(h, a) {
    const t = ticketsAtivos.find(x => x.hub === h && x.acao === a); if(!t) return;
    document.getElementById('config-hub').value = t.hub; document.getElementById('config-acao').value = t.acao; document.getElementById('config-tipo').value = t.tipo; mudarTipoTicket(); 
    let rs = {}; try { rs = typeof t.regras === 'string' ? JSON.parse(t.regras) : t.regras; } catch(e){}
    if (t.acao === 'relatorio' && rs.exportar_local) document.getElementById('config-destino').value = rs.exportar_local;
    if (t.tipo === 'rotina') {
        ['Segunda', 'Terca', 'Quarta', 'Quinta', 'Sexta', 'Sabado', 'Domingo'].forEach(d => {
            const ck = document.querySelector(`.chk-dia[value="${d}"]`), ct = document.getElementById(`slots-${d}`);
            if (rs[d] && rs[d].length > 0) {
                ck.checked = true; toggleDia(d); ct.innerHTML = ''; 
                rs[d].forEach(s => {
                    const nS = document.createElement('div'); nS.className = 'slot-row';
                    nS.innerHTML = `<span class="slot-label">Intervalo:</span><input type="number" class="int-val" value="${s.intervalo}" min="1"><span class="slot-label">min</span><span class="slot-label" style="margin-left:10px;">De:</span><input type="time" class="ini-val" value="${s.ini}"><span class="slot-label" style="margin-left:10px;">Até:</span><input type="time" class="fim-val" value="${s.fim}"><button onclick="this.parentElement.remove()" style="background: none; border: none; color: #ef4444; font-size: 14px; cursor: pointer; margin-left: auto;">❌</button>`;
                    ct.appendChild(nS);
                });
            } else {
                ck.checked = false; toggleDia(d);
                ct.innerHTML = `<div class="slot-row"><span class="slot-label">Intervalo:</span><input type="number" class="int-val" value="60" min="1"><span class="slot-label">min</span><span class="slot-label" style="margin-left:10px;">De:</span><input type="time" class="ini-val" value="08:00"><span class="slot-label" style="margin-left:10px;">Até:</span><input type="time" class="fim-val" value="18:00"><button onclick="this.parentElement.remove()" style="background: none; border: none; color: #ef4444; font-size: 14px; cursor: pointer; margin-left: auto;">❌</button></div>`;
            }
        });
    }
    document.getElementById('modal-overlay-config').style.display = 'block'; document.getElementById('painel-config-robo').classList.add('panel-as-modal');
    document.getElementById('titulo-config-robo').innerHTML = `✏️ Editando Rotina: <span style="color:#d97706;">${t.hub} - ${t.acao.toUpperCase()}</span>`;
    document.getElementById('btn-salvar-form').innerText = '💾 Atualizar Rotina'; document.getElementById('btn-cancelar-edicao').style.display = 'flex';
}
function fecharEdicaoTicket() {
    document.getElementById('modal-overlay-config').style.display = 'none'; document.getElementById('painel-config-robo').classList.remove('panel-as-modal');
    document.getElementById('titulo-config-robo').innerHTML = '⚙️ Configurar Nova Rotina'; document.getElementById('btn-salvar-form').innerText = '💾 Salvar Configuração do HUB';
    document.getElementById('btn-cancelar-edicao').style.display = 'none';
    if (!document.getElementById('config-hub').hasAttribute('readonly')) {
        document.getElementById('config-hub').value = '';
    }
}
function salvarConfiguracao(){ 
    let h = document.getElementById('config-hub').value.toUpperCase().trim(), a = document.getElementById('config-acao').value, t = document.getElementById('config-tipo').value;
    if (!h) h = "TODOS";

    let rs = { exportar_local: document.getElementById('config-destino').value };
    if (t === 'rotina') {
        document.querySelectorAll('.chk-dia:checked').forEach(ck => {
            let d = ck.value, ss = [];
            document.querySelectorAll(`#slots-${d} .slot-row`).forEach(rw => {
                let iv = rw.querySelector('.int-val').value, i = rw.querySelector('.ini-val').value, f = rw.querySelector('.fim-val').value;
                ss.push({ intervalo: parseInt(iv), ini: i, fim: f });
            });
            if(ss.length > 0) rs[d] = ss;
        });
        if (Object.keys(rs).length === 1) return alert("Ative e adicione pelo menos um horário em um dia da semana.");
    }
    fetch(`${API_BASE_URL}/add_ticket`,{ method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ hub: h, tipo: t, acao: a, regras: rs }) }).then(() => { fecharEdicaoTicket(); carregarTickets(); }); 
}
function removerTicket(h, a) { if(confirm(`Remover a tarefa de ${a.toUpperCase()} do HUB ${h}?`)) fetch(`${API_BASE_URL}/delete_ticket/${h}/${a}`, { method: 'DELETE' }).then(() => carregarTickets()); }