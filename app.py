"""
Fiche-Version Generator — Streamlit App
Run: streamlit run app.py
"""

import io
import re
import json
import time
import copy
import html as html_module
from collections import defaultdict
from datetime import datetime
from lxml import etree
import streamlit as st
from mistralai import Mistral
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# ─── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Fiche-Version Generator",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .block-container { padding-top: 1.5rem; }
  h1 { color: #1F497D; }

  .card {
    border: 1px solid rgba(128,128,128,0.25);
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 10px;
    background: var(--secondary-background-color);
    color: var(--text-color);
  }
  .card.relevant { border-left: 5px solid #2e7d32; }
  .card.skipped  { border-left: 5px solid #c62828; opacity: 0.85; }

  .badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 700;
    margin-right: 4px;
  }
  .badge-green  { background: rgba(46,125,50,0.15);   color: #4caf50; }
  .badge-red    { background: rgba(198,40,40,0.15);   color: #ef5350; }
  .badge-blue   { background: rgba(21,101,192,0.15);  color: #42a5f5; }
  .badge-grey   { background: rgba(128,128,128,0.15); color: var(--text-color); }
  .badge-orange { background: rgba(230,81,0,0.15);    color: #ffa726; }

  .release-note {
    background: rgba(46,125,50,0.1);
    border-left: 3px solid #558b2f;
    padding: 10px 14px;
    border-radius: 5px;
    margin-top: 10px;
    font-size: 14px;
    color: var(--text-color);
  }
  .reasoning-box {
    background: rgba(128,128,128,0.08);
    border-left: 3px solid #90a4ae;
    padding: 10px 14px;
    border-radius: 5px;
    margin-top: 8px;
    font-size: 13px;
    color: var(--text-color);
    opacity: 0.8;
    font-style: italic;
  }
  .skip-box {
    background: rgba(198,40,40,0.08);
    border-left: 3px solid #ef9a9a;
    padding: 10px 14px;
    border-radius: 5px;
    margin-top: 10px;
    font-size: 13px;
    color: #ef5350;
  }
  .card-title   { font-weight: 700; font-size: 15px; margin-bottom: 4px; color: var(--text-color); }
  .summary-text { color: var(--text-color); opacity: 0.75; font-size: 13px; margin-top: 4px; }
  .label-sm     { font-size: 10px; font-weight: 600; color: var(--text-color); opacity: 0.55; text-transform: uppercase; letter-spacing: 0.5px; }

  .section-header {
    font-size: 18px; font-weight: 700; color: #4a90d9;
    border-bottom: 2px solid rgba(74,144,217,0.3);
    padding-bottom: 6px; margin: 20px 0 14px 0;
  }

  .export-bar {
    background: rgba(33,150,243,0.08);
    border: 1px solid rgba(33,150,243,0.25);
    border-radius: 8px;
    padding: 14px 18px;
    margin: 16px 0;
    color: var(--text-color);
  }

  .stat-box {
    background: var(--secondary-background-color);
    border-radius: 8px;
    padding: 10px 16px; text-align: center;
  }
  .stat-num   { font-size: 28px; font-weight: 700; color: #4a90d9; }
  .stat-label { font-size: 12px; color: var(--text-color); opacity: 0.6; }

  div[data-testid="stProgress"] > div { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════
PROMPT_LONG = """\
Tu es un rédacteur technique expert spécialisé dans la production de fiches de version logicielles
pour un système ERP de l'administration publique française appelé GID
(Système de Gestion Intégré des Dépenses).

Tu reçois un ticket JIRA pré-traité, extrait d'un export XML.
Les champs ont déjà été nettoyés des informations sensibles (noms d'utilisateurs, URLs internes,
messages de déploiement).

TES DEUX SEULES TÂCHES :
1. Déterminer si le ticket décrit un changement réel et livré, pertinent pour la fiche de version.
2. Si pertinent, rédiger une phrase de note de version en français formel.

=== IMPORTANCE DES CHAMPS ===
Le Résumé (summary) et les 2-3 derniers commentaires sont les indicateurs les plus fiables
d'un contenu livré. La Description reflète souvent la demande initiale et peut ne pas
correspondre à ce qui a réellement été implémenté — utilise-la comme contexte secondaire.

=== RÈGLES DE PERTINENCE ===
PERTINENT : le ticket introduit un changement livré et visible par les utilisateurs finaux
ou les administrateurs (nouvelle fonctionnalité, amélioration, correction de bug,
changement d'interface ou de comportement).

NON PERTINENT — exemples typiques :
  • Discussion interne sans livraison concrète (ex : réunion de cadrage, échange de mails)
  • Tâche rejetée, reportée ou annulée
  • Dette technique sans impact utilisateur
  • Ticket en doublon
  • Résumé et description vides ou trop vagues pour identifier un changement fonctionnel


=== RÈGLES DE RÉDACTION (change_description) ===
- Français formel, 1-2 phrases, du point de vue de l'utilisateur final.
- Commence toujours par un verbe d'action nominalisé :
  Ajout de... | Correction de... | Optimisation de... | Mise à jour de...
  Possibilité de... | Amélioration de... | Prise en charge de...
- Précise le périmètre fonctionnel concerné.
- N'utilise PAS : numéros de ticket, noms de personnes, noms d'environnement
  (prod/recette/trunk), ni détails d'implémentation technique.

=== EXEMPLES ===

Exemple 1 — PERTINENT (amélioration) :
  Résumé   : MODIFICATION DU POSTE COMPTABLE VIA L'IHM
  Composant: Référentiel
  Commentaire : 'Traiter l'exception technique lors du changement de l'acteur comptable'
  → relevant: true
  → change_description: 'Possibilité de modifier le poste comptable assignataire via l'IHM,
    avec gestion des dates de début et de fin de relation.'

Exemple 2 — NON PERTINENT (discussion interne) :
  Résumé   : Point architecture future API
  Description: Réunion de cadrage. Rien de livré sur cette version.
  → relevant: false
  → reason_if_not_relevant: 'Discussion interne sans livraison fonctionnelle identifiable.'

Exemple 3 — PERTINENT malgré statut Réouvert :
  Résumé   : Correction calcul montant total dans le récapitulatif
  Commentaire: 'Bug corrigé dans la formule de calcul, vérifié sur plusieurs cas'
  → relevant: true
  → change_description: 'Correction du calcul du montant total dans le récapitulatif,
    prenant désormais en compte l'ensemble des lignes.'

=== FORMAT DE RÉPONSE ===
Réponds UNIQUEMENT avec un objet JSON valide, sans explication ni markdown :
{
  "relevant": true ou false,
  "change_description": "<phrase(s) de note de version en français formel>",
  "reason_if_not_relevant": "<raison brève si non pertinent, sinon null>",
  "reasoning": "<2-3 phrases : quel champ a orienté la décision, quel changement fonctionnel a été identifié, comment il a été formulé>"
}"""

PROMPT_SMALL = """\
Tu es un rédacteur technique expert en fiches de version logicielles pour le système GID (ERP de l'administration publique).

Tu reçois un ticket JIRA :
1. Évaluer la pertinence du ticket pour la fiche de version.
2. Si pertinent, rédiger une phrase de note de version.

IMPORTANT DES CHAMPS : Résumé + derniers commentaires = signal principal. Description = contexte secondaire.
Statut = à ignorer totalement.

PERTINENT : changement livré et visible (fonctionnalité, amélioration, correction, changement d'interface).
NON PERTINENT : discussion interne | tâche annulée/reportée | dette technique sans impact utilisateur | doublon | contenu trop vague.

RÉDACTION (change_description) :
- Français formel, 1-2 phrases, point de vue utilisateur final.
- Débuter par un verbe nominalisé : Ajout de… | Correction de… | Amélioration de… | Mise à jour de… | Prise en charge de…
- Préciser le périmètre fonctionnel. Aucun numéro de ticket, nom de personne, environnement ou détail technique.

RÉPONSE — JSON valide uniquement, sans markdown :
{
  "relevant": true | false,
  "change_description": "<note de version en français>",
  "reason_if_not_relevant": "<raison brève | null>",
  "reasoning": "<2-3 phrases : quel champ a orienté la décision, quel changement a été identifié, comment il a été formulé>"
}"""


# ═══════════════════════════════════════════════════════════════════════════════
# PREPROCESSING
# ═══════════════════════════════════════════════════════════════════════════════
SELECTED_TAGS = [
    ('key',         'key'),
    ('type',        'type'),
    ('summary',     'summary'),
    ('description', 'description'),
    ('component',   'component'),
    ('comments',    'comments'),
]

SENSITIVE_KEYWORDS = [
    'recette', 'déploiement', 'déployé en', 'branch', 'trunk version',
    'salam,', 'bonjour,', 'envoyé :', 'cordialement,', 'objet :',
    'test ok', 'oui, effectivement', 'voir le document ci-joint', 'me contacter',
    'voir retour de', 'suite à notre discussion',
]

CLEAN_PATTERNS = [
    r'https?://\S+',
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
    r'\bSGID-\d+\b',
    r'\bREC-\d+\b',
    r'MC\.[a-zA-Z.]+',
    r'\[.*?\]',
    r'(?:login|username|utilisateur|user)\s*(?:comptable|ordonnateur)?\s*:?\s*[a-zA-Z]+\.[a-zA-Z]+[^\w]*',
    r'\b[A-Za-z]+\.[A-Za-z]+\b',
    r'(\d+)/(\d{4})\b',
    r'\b\d+(?:[\s.,]?\d+)*(?:\s*(?:MAD|DHs|dhs?|\$))?\b',
]

ANOMALY_TYPES = {'bug', 'bogue'}
RELEVANT_JIRA_TYPES = {'Amélioration', 'Bug', 'Anomalie', 'Bogue', 'Tâche', 'Sous-Tâche'}
FIELDS_TO_CLEAN = ['description', 'comments', 'summary']


def strip_html(raw: str) -> str:
    decoded = html_module.unescape(raw or '')
    clean = re.sub(r'<[^>]+>', ' ', decoded)
    return re.sub(r'\s+', ' ', clean).strip()


def extract_comments(item_el) -> str:
    block = item_el.find('comments')
    if block is None:
        return ''
    parts = [strip_html(c.text or '') for c in block.findall('comment') if c.text]
    return ' | '.join(filter(None, parts))


def parse_xml_bytes(content: bytes) -> list[dict]:
    root = etree.fromstring(content)
    tickets = []
    for item in root.iter('item'):
        ticket = {}
        for field_name, xml_tag in SELECTED_TAGS:
            if field_name == 'comments':
                ticket['comments'] = extract_comments(item)
            else:
                el = item.find(xml_tag)
                raw = (el.text or '') if el is not None else ''
                ticket[field_name] = strip_html(raw)
        tickets.append(ticket)
    return tickets


def clean_text(text: str) -> str:
    if not text:
        return text
    for pattern in CLEAN_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    separators = r'(?<=[.!?])\s+|\s*\|\s*|\n+'
    segments = re.split(separators, text)
    filtered = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if any(kw in seg.lower() for kw in SENSITIVE_KEYWORDS):
            continue
        filtered.append(seg)
    result = ' | '.join(filtered) if '|' in text else ' '.join(filtered)
    return re.sub(r'\s+', ' ', result).strip()


def clean_ticket(ticket: dict) -> dict:
    cleaned = copy.deepcopy(ticket)
    for field in FIELDS_TO_CLEAN:
        cleaned[field] = clean_text(cleaned.get(field, ''))
    return cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# JIRA API FETCH
# ═══════════════════════════════════════════════════════════════════════════════
def fetch_tickets_from_jira_api(
    server: str,
    username: str,
    password: str,
    ticket_codes: list[str],
    status_placeholder=None,
) -> tuple[list[dict], list[str]]:
    """
    Fetch tickets from a JIRA server via the REST API.
    Returns (tickets, warnings) where warnings is a list of skipped ticket keys.
    Raises RuntimeError on connection/auth failures.
    """
    import requests
    import urllib3

    def _log(msg: str):
        if status_placeholder:
            status_placeholder.info(msg)

    server = server.rstrip('/')

    # ── 1. Connectivity check ────────────────────────────────────────────────
    _log(f"🔌 Vérification de la connexion à {server} …")
    verify_ssl = True
    try:
        r = requests.get(
            f'{server}/rest/api/2/serverInfo',
            auth=(username, password),
            timeout=15,
            verify=True,
        )
        r.raise_for_status()
        info = r.json()
        _log(f"✅ Connecté — JIRA {info.get('version', '?')} · {info.get('serverTitle', '')}")
        verify_ssl = True

    except requests.exceptions.SSLError:
        _log("⚠️ Certificat SSL auto-signé détecté — nouvelle tentative sans vérification SSL…")
        urllib3.disable_warnings()
        try:
            r = requests.get(
                f'{server}/rest/api/2/serverInfo',
                auth=(username, password),
                timeout=15,
                verify=False,
            )
            r.raise_for_status()
            _log(f"✅ Connecté (SSL non vérifié) — JIRA {r.json().get('version', '?')}")
            verify_ssl = False
        except Exception as e2:
            raise RuntimeError(f"Connexion échouée même sans vérification SSL : {e2}")

    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(
            f"Impossible de joindre {server}.\n"
            f"Causes possibles : URL incorrecte, VPN requis, pare-feu, ou réseau non autorisé.\n"
            f"Détail : {e}"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"Le serveur ne répond pas (timeout). "
            f"Cette machine n'est peut-être pas sur le même réseau/VPN que {server}. "
            f"Utilisez l'export XML à la place."
        )
    except requests.exceptions.HTTPError as e:
        sc = r.status_code
        if sc == 401:
            raise RuntimeError("Authentification échouée (401). Vérifiez identifiant et mot de passe.")
        elif sc == 403:
            raise RuntimeError("Accès refusé (403). Votre compte n'a peut-être pas accès à l'API REST.")
        else:
            raise RuntimeError(f"Erreur HTTP {sc} : {e}")

    # ── 2. Connect via jira library ──────────────────────────────────────────
    try:
        from jira import JIRA
    except ImportError:
        raise RuntimeError(
            "Le module 'jira' n'est pas installé. Exécutez : pip install jira"
        )

    options = {'server': server, 'verify': verify_ssl}
    jira_client = JIRA(options=options, basic_auth=(username, password), timeout=30)

    # ── 3. JQL fetch ─────────────────────────────────────────────────────────
    codes_jql = ', '.join(ticket_codes)
    jql = f'issueKey in ({codes_jql})'
    _log(f"🔍 Récupération JQL : {jql}")

    issues = jira_client.search_issues(
        jql,
        maxResults=len(ticket_codes) + 10,
        fields='summary,description,issuetype,components,status,resolution,fixVersions,comment',
    )
    _log(f"→ {len(issues)} issue(s) retournée(s)")

    # ── 4. Build ticket dicts ─────────────────────────────────────────────────
    tickets = []
    warnings = []
    for issue in issues:
        issue_type = issue.fields.issuetype.name
        if issue_type not in RELEVANT_JIRA_TYPES:
            warnings.append(f"{issue.key} ignoré (type : {issue_type})")
            continue

        comments = ' | '.join(
            c.body for c in (issue.fields.comment.comments or [])
            if c.body
        )

        tickets.append({
            'key':         issue.key,
            'type':        issue_type,
            'summary':     issue.fields.summary or '',
            'description': issue.fields.description or '',
            'component':   ', '.join(c.name for c in (issue.fields.components or [])) or 'Autres évolutions',
            'resolution':  getattr(issue.fields.resolution, 'name', 'Non résolue'),
            'fixVersion':  ', '.join(v.name for v in (issue.fields.fixVersions or [])),
            'comments':    comments,
        })

    return tickets, warnings


# ═══════════════════════════════════════════════════════════════════════════════
# LLM
# ═══════════════════════════════════════════════════════════════════════════════
def build_user_message(ticket: dict) -> str:
    return (
        f"Analyse ce ticket JIRA et produis une entrée de note de version.\n\n"
        f"Clé          : {ticket.get('key', '')}\n"
        f"Type         : {ticket.get('type', '')}\n"
        f"Composant    : {ticket.get('component', '')}\n\n"
        f"Résumé :\n{ticket.get('summary', '')}\n\n"
        f"Description :\n{ticket.get('description', '')[:1200]}\n\n"
        f"Commentaires pertinents :\n{ticket.get('comments', '')[:2500]}"
    )


def analyze_ticket(client: Mistral, model: str, system_prompt: str,
                   ticket: dict, delay: float, retries: int = 4) -> dict:
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user',   'content': build_user_message(ticket)},
    ]
    for attempt in range(retries):
        try:
            response = client.chat.complete(
                model=model,
                messages=messages,
                response_format={'type': 'json_object'},
                max_tokens=512,
                temperature=0.2,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
            result = json.loads(raw)
            result['key']       = ticket.get('key', '')
            result['component'] = ticket.get('component', '') or 'Autres évolutions'
            result['type']      = ticket.get('type', '')
            return result
        except Exception as e:
            err = str(e)
            is_rate = '429' in err
            wait = min(60, 2 ** (attempt + 2)) if is_rate else 2 ** attempt
            time.sleep(wait)
    return {
        'key': ticket.get('key', ''), 'relevant': False,
        'change_description': None, 'reasoning': None,
        'component': ticket.get('component', '') or 'Autres évolutions',
        'type': ticket.get('type', ''),
        'reason_if_not_relevant': 'Appel Mistral échoué après plusieurs tentatives.',
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DOCX GENERATION
# ═══════════════════════════════════════════════════════════════════════════════
def set_cell_bg(cell, hex_color: str):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_color)
    tcPr.append(shd)


def add_component_section(doc, component_name: str, changes: list):
    heading = doc.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = heading.add_run(f'Module « {component_name} »')
    run.bold = True
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)

    intro = doc.add_paragraph('Ce module intègre les évolutions suivantes dans cette version :')
    intro.paragraph_format.space_before = Pt(0)
    intro.paragraph_format.space_after  = Pt(2)

    for change in changes:
        bullet = doc.add_paragraph(style='List Bullet')
        bullet.add_run(change)
        bullet.paragraph_format.space_after = Pt(2)

    doc.add_paragraph('').paragraph_format.space_after = Pt(4)


def generate_docx_bytes(selected_results: list, version_label: str, deploy_date: str) -> bytes:
    doc = Document()
    for section in doc.sections:
        section.top_margin    = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)

    tt = doc.add_table(rows=2, cols=2)
    tt.style = 'Table Grid'
    c_date  = tt.cell(0, 0);  c_date.text  = f'Date : {deploy_date}'
    c_title = tt.cell(0, 1);  c_title.text = 'Fiche de version GID'
    set_cell_bg(c_date,  '1F497D')
    set_cell_bg(c_title, '1F497D')
    for cell in [c_date, c_title]:
        for run in cell.paragraphs[0].runs:
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            run.bold = True
            run.font.size = Pt(13)
    desc_cell = tt.cell(1, 0)
    desc_cell.merge(tt.cell(1, 1))
    desc_cell.text = (
        f'La version {version_label} déployée en production le {deploy_date} '
        f'intègre les fonctionnalités suivantes :'
    )
    set_cell_bg(desc_cell, 'D0E4F7')
    doc.add_paragraph('')

    component_changes: dict = defaultdict(list)
    anomaly_changes: list   = []
    for r in selected_results:
        is_anom = r.get('type', '').lower() in ANOMALY_TYPES
        if is_anom:
            anomaly_changes.append((r['component'], r['change_description']))
        else:
            component_changes[r['component']].append(r['change_description'])

    for comp in sorted(component_changes.keys()):
        add_component_section(doc, comp, component_changes[comp])

    if anomaly_changes:
        ah = doc.add_paragraph()
        run = ah.add_run('Anomalies :')
        run.bold = True; run.font.size = Pt(12)
        run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
        intro = doc.add_paragraph('Cette version corrige également les anomalies se rapportant à :')
        intro.paragraph_format.space_after = Pt(2)
        for comp, change in anomaly_changes:
            b = doc.add_paragraph(style='List Bullet')
            b.add_run(f'[{comp}] {change}' if comp else change)
            b.paragraph_format.space_after = Pt(2)

    doc.add_paragraph('')
    fp = doc.add_paragraph(
        f'Document généré automatiquement le {datetime.now().strftime("%d/%m/%Y à %H:%M")}'
    )
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = fp.runs[0]
    fr.font.size = Pt(8)
    fr.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    fr.italic = True

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# CARD RENDERER
# ═══════════════════════════════════════════════════════════════════════════════
def render_card(result: dict):
    key         = result.get('key', '?')
    relevant    = result.get('relevant', False)
    component   = result.get('component', '—')
    ticket_type = result.get('type', '—')
    change      = result.get('change_description') or ''
    reasoning   = result.get('reasoning') or ''
    skip_rsn    = result.get('reason_if_not_relevant') or ''
    is_anom     = ticket_type.lower() in ANOMALY_TYPES

    card_class = 'card relevant' if relevant else 'card skipped'
    badge_rel  = '<span class="badge badge-green">✅ PERTINENT</span>' if relevant else '<span class="badge badge-red">⏭ IGNORÉ</span>'
    badge_comp = f'<span class="badge badge-blue">{component}</span>'
    badge_type = f'<span class="badge badge-orange">{ticket_type}</span>' if is_anom else f'<span class="badge badge-grey">{ticket_type}</span>'

    content_html = ''
    if relevant and change:
        content_html += f'<div class="release-note"><span class="label-sm">Note de version</span><br>{change}</div>'
    if not relevant and skip_rsn:
        content_html += f'<div class="skip-box"><span class="label-sm">Raison</span><br>{skip_rsn}</div>'
    if reasoning:
        content_html += f'<div class="reasoning-box"><span class="label-sm">Raisonnement du modèle</span><br>{reasoning}</div>'

    st.markdown(f"""
    <div class="{card_class}">
      <div class="card-title">{key} &nbsp; {badge_rel} &nbsp; {badge_comp} &nbsp; {badge_type}</div>
      {content_html}
    </div>
    """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION STATE INIT
# ═══════════════════════════════════════════════════════════════════════════════
for _k in ('all_results', 'processed', 'cleaned_tickets'):
    if _k not in st.session_state:
        st.session_state[_k] = [] if _k != 'processed' else False


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## ⚙️ Configuration")
    st.divider()

    # ── Mistral ───────────────────────────────────────────────────────────────
    api_key = st.text_input(
        "Clé API Mistral",
        type="password",
        placeholder="Votre clé depuis console.mistral.ai",
        help="Clé gratuite sur console.mistral.ai"
    )

    model_choice = st.selectbox(
        "Modèle",
        options=['mistral-small-latest', 'mistral-medium-latest', 'open-mistral-7b', 'open-mixtral-8x7b'],
        index=0,
    )

    prompt_choice = st.radio(
        "Version du prompt",
        options=["🔹 Court (moins de tokens)", "🔷 Long (plus de contexte)"],
        index=0,
        help="Le prompt long inclut des exemples few-shot et des règles détaillées."
    )
    selected_prompt = PROMPT_LONG if "Long" in prompt_choice else PROMPT_SMALL

    st.divider()

    # ── Source des tickets ────────────────────────────────────────────────────
    st.markdown("### 📥 Source des tickets")
    ticket_source = st.radio(
        "Mode d'import",
        options=["📁 Export XML", "🔌 API JIRA (réseau interne)"],
        index=0,
        help="L'API JIRA nécessite d'être sur le réseau interne ou le VPN de votre organisation."
    )
    use_jira_api = "API JIRA" in ticket_source

    if use_jira_api:
        st.markdown("#### Connexion JIRA")
        jira_server   = st.text_input("URL du serveur", placeholder="https://jira.votreorganisation.ma")
        jira_username = st.text_input("Identifiant", placeholder="prenom.nom")
        jira_password = st.text_input("Mot de passe / Token", type="password")
        jira_codes_raw = st.text_area(
            "Codes tickets (un par ligne)",
            placeholder="SGID-61147\nSGID-61065\nSGID-61188",
            height=120,
            help="Entrez les identifiants JIRA, un par ligne."
        )
        jira_ticket_codes = [
            c.strip() for c in jira_codes_raw.splitlines() if c.strip()
        ]
        jira_ready = bool(jira_server and jira_username and jira_password and jira_ticket_codes)
    else:
        jira_server = jira_username = jira_password = ""
        jira_ticket_codes = []
        jira_ready = False

    st.divider()

    # ── Document ──────────────────────────────────────────────────────────────
    st.markdown("### 📄 Document")
    version_label = st.text_input("Label de version", value="Trunk")
    deploy_date   = st.text_input("Date de déploiement", value=datetime.now().strftime("%d/%m/%Y"))
    inter_delay   = 1.2


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("# 📋 Fiche-Version Generator")
st.markdown("Importez vos tickets JIRA (XML ou API directe), analysez-les avec Mistral, sélectionnez et exportez.")
st.divider()

# ── Step 1: Import ─────────────────────────────────────────────────────────────
if use_jira_api:
    st.markdown('<div class="section-header">① Connexion API JIRA</div>', unsafe_allow_html=True)

    if jira_ready:
        st.success(
            f"✅ **{len(jira_ticket_codes)}** ticket(s) configuré(s) · "
            f"serveur : `{jira_server}`"
        )
        st.caption("La connexion sera établie lors de l'analyse. Assurez-vous d'être sur le réseau interne / VPN.")
    else:
        st.info("🔌 Renseignez les paramètres JIRA dans la barre latérale (URL, identifiant, mot de passe, codes tickets).")

    uploaded_files = []  # not used in JIRA mode
    source_ready   = jira_ready

else:
    st.markdown('<div class="section-header">① Importer les tickets XML</div>', unsafe_allow_html=True)

    uploaded_files = st.file_uploader(
        "Glissez-déposez un ou plusieurs fichiers XML JIRA",
        type=["xml"],
        accept_multiple_files=True,
        help="Export les Tickets JIRA au format XML — un ou plusieurs fichiers"
    )

    if uploaded_files:
        st.success(f"✅ **{len(uploaded_files)}** fichier(s) prêts à être traités.")

    source_ready = bool(uploaded_files)

# ── Step 2: Process ────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">② Analyser avec Mistral</div>', unsafe_allow_html=True)

col_btn, col_info = st.columns([2, 5])
with col_btn:
    run_btn = st.button(
        "▶ Analyser les tickets",
        type="primary",
        disabled=not (source_ready and api_key),
        use_container_width=True,
    )
with col_info:
    if not api_key:
        st.warning("⚠️ Renseignez votre clé API Mistral dans la barre latérale.")
    elif not source_ready:
        if use_jira_api:
            st.info("🔌 Complétez la configuration JIRA dans la barre latérale.")
        else:
            st.info("📂 Importez au moins un fichier XML.")
    else:
        if use_jira_api:
            n = len(jira_ticket_codes)
        else:
            n = len(uploaded_files)
        est = int(n * inter_delay) + n * 2
        st.info(f"Prêt · {n} ticket(s) · durée estimée ~{est}s à {inter_delay}s/appel")

if run_btn and source_ready and api_key:

    raw_tickets = []

    if use_jira_api:
        # ── JIRA API path ──────────────────────────────────────────────────
        jira_status = st.empty()
        try:
            fetched, fetch_warnings = fetch_tickets_from_jira_api(
                server=jira_server,
                username=jira_username,
                password=jira_password,
                ticket_codes=jira_ticket_codes,
                status_placeholder=jira_status,
            )
            raw_tickets = fetched
            jira_status.empty()
            if fetch_warnings:
                for w in fetch_warnings:
                    st.warning(f"⏭ {w}")
            st.success(f"✅ **{len(raw_tickets)}** ticket(s) récupéré(s) depuis JIRA.")
        except RuntimeError as e:
            jira_status.empty()
            st.error(f"❌ Erreur API JIRA : {e}")
            st.info(
                "💡 **Alternative** : exportez les tickets en XML depuis JIRA "
                "(Détails du ticket → ⋮ → Exporter en XML) et utilisez le mode Import XML."
            )
            st.stop()
    else:
        # ── XML path ──────────────────────────────────────────────────────
        for f in uploaded_files:
            try:
                parsed = parse_xml_bytes(f.read())
                raw_tickets.extend(parsed)
            except Exception as e:
                st.error(f"❌ Erreur parsing `{f.name}`: {e}")

    cleaned = [clean_ticket(t) for t in raw_tickets]

    # ── Deduplicate ────────────────────────────────────────────────────────
    seen_keys: set = set()
    deduped, duplicates = [], []
    for t in cleaned:
        k = t.get('key', '')
        if k and k in seen_keys:
            duplicates.append(k)
        else:
            if k:
                seen_keys.add(k)
            deduped.append(t)
    cleaned = deduped
    if duplicates:
        st.warning(
            f"⚠️ **{len(duplicates)}** ticket(s) en doublon ignoré(s) "
            f"(première occurrence conservée) : {', '.join(duplicates)}"
        )

    st.session_state.cleaned_tickets = cleaned

    if not cleaned:
        st.error("Aucun ticket valide trouvé.")
    else:
        client = Mistral(api_key=api_key)
        all_results = []
        progress_bar = st.progress(0, text="Initialisation…")
        status_area  = st.empty()

        for i, ticket in enumerate(cleaned):
            status_area.markdown(
                f"🔄 Analyse de **{ticket.get('key', '?')}** "
                f"[{ticket.get('component', '')}] · {i+1}/{len(cleaned)}"
            )
            result = analyze_ticket(client, model_choice, selected_prompt, ticket, inter_delay)
            all_results.append(result)

            cb_key = f"sel_{result['key']}"
            if cb_key not in st.session_state:
                st.session_state[cb_key] = result.get('relevant', False)

            pct = (i + 1) / len(cleaned)
            progress_bar.progress(pct, text=f"Traités : {i+1}/{len(cleaned)}")
            if i < len(cleaned) - 1:
                time.sleep(inter_delay)

        st.session_state.all_results = all_results
        st.session_state.processed   = True
        progress_bar.empty()
        status_area.empty()
        st.success(
            f"✅ Analyse terminée — "
            f"{sum(1 for r in all_results if r.get('relevant'))} pertinents · "
            f"{sum(1 for r in all_results if not r.get('relevant'))} ignorés"
        )
        st.rerun()


# ── Step 3: Results ────────────────────────────────────────────────────────────
if st.session_state.get('processed') and st.session_state.get('all_results'):
    all_results = st.session_state.all_results
    relevant = [r for r in all_results if r.get('relevant')]
    skipped  = [r for r in all_results if not r.get('relevant')]

    st.markdown('<div class="section-header">③ Résultats et sélection</div>', unsafe_allow_html=True)
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        st.markdown(f'<div class="stat-box"><div class="stat-num">{len(all_results)}</div><div class="stat-label">Tickets analysés</div></div>', unsafe_allow_html=True)
    with s2:
        st.markdown(f'<div class="stat-box"><div class="stat-num" style="color:#2e7d32">{len(relevant)}</div><div class="stat-label">Pertinents</div></div>', unsafe_allow_html=True)
    with s3:
        st.markdown(f'<div class="stat-box"><div class="stat-num" style="color:#c62828">{len(skipped)}</div><div class="stat-label">Ignorés</div></div>', unsafe_allow_html=True)
    components = sorted(set(r['component'] for r in relevant))
    with s4:
        st.markdown(f'<div class="stat-box"><div class="stat-num">{len(components)}</div><div class="stat-label">Composants</div></div>', unsafe_allow_html=True)

    st.markdown("")

    ca_col, _, filter_col = st.columns([2, 5, 3])
    with ca_col:
        select_all = st.checkbox("Tout sélectionner", value=False)
        if select_all != st.session_state.get("_select_all_prev", False):
            st.session_state["_select_all_prev"] = select_all
            for r in all_results:
                st.session_state[f"sel_{r['key']}"] = select_all
            st.rerun()
    with filter_col:
        show_skipped = st.toggle("Afficher les tickets ignorés", value=False)

    st.markdown("")

    display_results = all_results if show_skipped else relevant
    by_component: dict = defaultdict(list)
    for r in display_results:
        by_component[r['component']].append(r)

    for comp in sorted(by_component.keys()):
        comp_results = by_component[comp]
        with st.expander(f"**{comp}** — {len(comp_results)} ticket(s)", expanded=True):
            for result in comp_results:
                key = result.get('key', '?')
                cb_col, card_col = st.columns([1, 20])
                with cb_col:
                    st.checkbox("", key=f"sel_{key}")
                with card_col:
                    render_card(result)

    # ── Step 4: Export ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">④ Exporter la Fiche de Version</div>', unsafe_allow_html=True)

    selected_results = [
        r for r in all_results
        if st.session_state.get(f"sel_{r.get('key', '')}", False)
        and r.get('relevant') and r.get('change_description')
    ]

    st.markdown(f"""
    <div class="export-bar">
      <strong>{len(selected_results)}</strong> ticket(s) sélectionné(s) pour l'export
      · groupés en <strong>{len(set(r['component'] for r in selected_results))}</strong> composant(s)
    </div>
    """, unsafe_allow_html=True)

    if selected_results:
        docx_bytes = generate_docx_bytes(selected_results, version_label, deploy_date)
        filename   = f"Fiche_Version_GID_{version_label.replace(' ', '_')}_{deploy_date.replace('/', '-')}.docx"

        st.download_button(
            label="⬇ Télécharger la Fiche de Version (.docx)",
            data=docx_bytes,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
            use_container_width=False,
        )
        st.caption(f"Fichier : `{filename}` · {len(selected_results)} entrées · {len(set(r['component'] for r in selected_results))} modules")
    else:
        st.info("Sélectionnez au moins un ticket pertinent pour générer le document.")
