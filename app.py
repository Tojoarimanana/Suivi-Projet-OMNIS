from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="Suivi des Compagnies",
    page_icon="chart_with_upwards_trend",
    layout="wide",
    initial_sidebar_state="expanded",
)


@dataclass
class WorkbookData:
    projects: pd.DataFrame
    updates: pd.DataFrame
    source_sheet: str


BASE_COLUMN_MAP = {
    0: "compagnie_projet",
    1: "secteur",
    2: "ressource_cible",
    3: "objectif",
    4: "zone_interet",
    5: "date_reference",
    6: "dossier_soumis",
    7: "investissement_initial_musd",
    8: "travaux_proposes",
}


DISPLAY_LABELS = {
    "compagnie_projet": "Compagnie / Projet",
    "secteur": "Secteur",
    "ressource_cible": "Ressource cible",
    "objectif": "Objectif",
    "zone_interet": "Zone d'interet",
    "date_reference": "Date de reference",
    "dossier_soumis": "Dossier soumis",
    "investissement_initial_musd": "Investissement initial (M USD)",
    "travaux_proposes": "Travaux proposes",
    "statut_actuel": "Statut actuel",
    "nb_mises_a_jour": "Nb mises a jour",
    "derniere_mise_a_jour": "Derniere mise a jour",
}

OMNIS_LOGO_PATH = Path(__file__).with_name("logo.svg")


def normalize_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).replace("\n", " ").strip()
    if text.lower() == "nan":
        return None
    return text or None


def parse_date(value: object) -> pd.Timestamp | pd.NaT:
    if value in (None, "", "nan"):
        return pd.NaT

    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return pd.NaT
    return parsed


def pick_sheet_name(sheets: dict[str, pd.DataFrame]) -> str:
    for sheet_name, df in sheets.items():
        sample = df.head(3).fillna("").astype(str).apply(
            lambda col: " ".join(col).lower(), axis=0
        )
        joined = " ".join(sample.tolist())
        if "compagnie" in joined and "objectif" in joined:
            return sheet_name
    return next(iter(sheets))


def build_projects_dataframe(raw_df: pd.DataFrame) -> WorkbookData:
    if raw_df.shape[0] < 3:
        raise ValueError("La feuille selectionnee ne contient pas assez de lignes.")

    working = raw_df.copy()
    working = working.dropna(how="all").dropna(axis=1, how="all")
    working = working.reset_index(drop=True)

    projects_rows = working.iloc[2:].copy().reset_index(drop=True)
    base_col_count = min(len(BASE_COLUMN_MAP), projects_rows.shape[1])

    renamed_columns: list[str] = []
    for idx in range(projects_rows.shape[1]):
        if idx < base_col_count:
            renamed_columns.append(BASE_COLUMN_MAP[idx])
        else:
            relative_idx = idx - base_col_count
            group_no = (relative_idx // 2) + 1
            field_name = "update_date" if relative_idx % 2 == 0 else "update_activity"
            renamed_columns.append(f"{field_name}_{group_no}")

    projects_rows.columns = renamed_columns

    for column in projects_rows.columns:
        if column == "investissement_initial_musd":
            projects_rows[column] = pd.to_numeric(projects_rows[column], errors="coerce")
        elif column == "date_reference" or column.startswith("update_date_"):
            projects_rows[column] = projects_rows[column].apply(parse_date)
        else:
            projects_rows[column] = projects_rows[column].apply(normalize_text)

    projects_rows = projects_rows.dropna(subset=["compagnie_projet"]).reset_index(drop=True)

    updates = extract_updates(projects_rows)
    projects = enrich_projects(projects_rows, updates)
    return WorkbookData(projects=projects, updates=updates, source_sheet="")


def extract_updates(projects_df: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    update_date_cols = sorted(col for col in projects_df.columns if col.startswith("update_date_"))

    for _, row in projects_df.iterrows():
        for date_col in update_date_cols:
            activity_col = date_col.replace("update_date_", "update_activity_")
            update_index = int(date_col.rsplit("_", 1)[-1])
            update_date = row.get(date_col)
            update_activity = normalize_text(row.get(activity_col))

            if pd.isna(update_date) and not update_activity:
                continue

            records.append(
                {
                    "compagnie_projet": row["compagnie_projet"],
                    "secteur": row.get("secteur"),
                    "objectif": row.get("objectif"),
                    "zone_interet": row.get("zone_interet"),
                    "ordre_mise_a_jour": update_index,
                    "date_mise_a_jour": update_date,
                    "activite": update_activity,
                }
            )

    updates_df = pd.DataFrame(records)
    if updates_df.empty:
        return pd.DataFrame(
            columns=[
                "compagnie_projet",
                "secteur",
                "objectif",
                "zone_interet",
                "ordre_mise_a_jour",
                "date_mise_a_jour",
                "activite",
            ]
        )

    return updates_df.sort_values(
        by=["compagnie_projet", "date_mise_a_jour", "ordre_mise_a_jour"],
        ascending=[True, True, True],
        na_position="last",
    ).reset_index(drop=True)


def build_current_status(row: pd.Series, project_updates: pd.DataFrame) -> str:
    with_activity = project_updates[project_updates["activite"].notna()]
    if not with_activity.empty:
        return with_activity.iloc[-1]["activite"]
    if row.get("travaux_proposes"):
        return str(row["travaux_proposes"])
    if row.get("dossier_soumis"):
        return str(row["dossier_soumis"])
    return "A definir"


def enrich_projects(projects_df: pd.DataFrame, updates_df: pd.DataFrame) -> pd.DataFrame:
    enriched = projects_df.copy()
    update_counts = updates_df.groupby("compagnie_projet").size().rename("nb_mises_a_jour")
    latest_update = (
        updates_df.sort_values(
            by=["date_mise_a_jour", "ordre_mise_a_jour"],
            ascending=[True, True],
            na_position="last",
        )
        .groupby("compagnie_projet")
        .tail(1)
        .set_index("compagnie_projet")
    )

    enriched["nb_mises_a_jour"] = (
        enriched["compagnie_projet"].map(update_counts).fillna(0).astype(int)
    )
    enriched["derniere_mise_a_jour"] = enriched["compagnie_projet"].map(
        latest_update["date_mise_a_jour"] if not latest_update.empty else pd.Series(dtype="datetime64[ns]")
    )

    statuses: list[str] = []
    for _, row in enriched.iterrows():
        project_updates = updates_df[updates_df["compagnie_projet"] == row["compagnie_projet"]]
        statuses.append(build_current_status(row, project_updates))
    enriched["statut_actuel"] = statuses

    return enriched


@st.cache_data(show_spinner=False)
def load_workbook(uploaded_file) -> WorkbookData:
    sheets = pd.read_excel(uploaded_file, sheet_name=None, header=None)
    sheet_name = pick_sheet_name(sheets)
    workbook_data = build_projects_dataframe(sheets[sheet_name])
    workbook_data.source_sheet = sheet_name
    return workbook_data


def format_date(value: object) -> str:
    if pd.isna(value):
        return "-"
    return pd.to_datetime(value).strftime("%d/%m/%Y")


def get_logo_data_uri() -> str:
    svg_bytes = OMNIS_LOGO_PATH.read_bytes()
    encoded = base64.b64encode(svg_bytes).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def to_downloadable_excel(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def filter_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Filtres")

    companies = st.sidebar.multiselect(
        "Compagnies / projets",
        sorted(df["compagnie_projet"].dropna().unique().tolist()),
    )
    sectors = st.sidebar.multiselect(
        "Secteurs",
        sorted(df["secteur"].dropna().unique().tolist()),
    )
    resources = st.sidebar.multiselect(
        "Ressources",
        sorted(df["ressource_cible"].dropna().unique().tolist()),
    )
    objectives = st.sidebar.multiselect(
        "Objectifs",
        sorted(df["objectif"].dropna().unique().tolist()),
    )
    zones = st.sidebar.multiselect(
        "Zones d'interet",
        sorted(df["zone_interet"].dropna().unique().tolist()),
    )
    statuses = st.sidebar.multiselect(
        "Statuts actuels",
        sorted(df["statut_actuel"].dropna().unique().tolist()),
    )
    search = st.sidebar.text_input("Recherche libre")

    filtered = df.copy()
    if companies:
        filtered = filtered[filtered["compagnie_projet"].isin(companies)]
    if sectors:
        filtered = filtered[filtered["secteur"].isin(sectors)]
    if resources:
        filtered = filtered[filtered["ressource_cible"].isin(resources)]
    if objectives:
        filtered = filtered[filtered["objectif"].isin(objectives)]
    if zones:
        filtered = filtered[filtered["zone_interet"].isin(zones)]
    if statuses:
        filtered = filtered[filtered["statut_actuel"].isin(statuses)]
    if search:
        mask = filtered.fillna("").astype(str).apply(
            lambda col: col.str.contains(search, case=False, na=False)
        )
        filtered = filtered[mask.any(axis=1)]

    return filtered.reset_index(drop=True)


def render_header() -> None:
    logo_src = get_logo_data_uri()
    header_html = """
        <style>
        .app-header {
            position: sticky;
            top: 0;
            z-index: 999;
            display: flex;
            align-items: center;
            gap: 1.25rem;
            background: rgba(255, 255, 255, 0.96);
            backdrop-filter: blur(8px);
            padding: 0.8rem 0;
            margin-bottom: 0.8rem;
            border-bottom: 1px solid #e5e7eb;
        }
        .app-header-logo {
            width: 120px;
            max-width: 24vw;
            height: auto;
            flex: 0 0 auto;
            display: block;
        }
        .app-header-text {
            min-width: 0;
        }
        .sticky-footer {
            position: fixed;
            left: 0;
            bottom: 0;
            width: 100%;
            z-index: 998;
            background: rgba(15, 23, 42, 0.96);
            color: #ffffff;
            text-align: center;
            padding: 0.55rem 1rem;
            font-size: 0.9rem;
            border-top: 1px solid rgba(255, 255, 255, 0.12);
            backdrop-filter: blur(8px);
        }
        .main-title {
            font-size: 2.2rem;
            font-weight: 700;
            margin: 0 0 0.2rem 0;
            line-height: 1.15;
        }
        .sub-title {
            color: #4b5563;
            margin: 0;
        }
        .card {
            background: linear-gradient(135deg, #f8fafc 0%, #eef6ff 100%);
            border: 1px solid #dbe7f5;
            border-radius: 18px;
            padding: 1rem 1.2rem;
            box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
        }
        .stDownloadButton > button,
        .stFileUploader,
        .stDataFrame,
        .stTabs {
            width: 100%;
        }
        .block-container {
            padding-bottom: 4.25rem;
        }
        @media (max-width: 1024px) {
            .app-header-logo {
                width: 100px;
            }
            .main-title {
                font-size: 1.9rem;
            }
            .sub-title {
                font-size: 0.95rem;
            }
        }
        @media (max-width: 768px) {
            .app-header {
                gap: 0.8rem;
                padding: 0.65rem 0;
                align-items: flex-start;
            }
            .app-header-logo {
                width: 76px;
                max-width: 20vw;
            }
            .main-title {
                font-size: 1.55rem;
            }
            .sub-title {
                font-size: 0.9rem;
            }
            .card {
                padding: 0.85rem 1rem;
                border-radius: 14px;
            }
            div[data-testid="stHorizontalBlock"] {
                flex-wrap: wrap;
                gap: 0.75rem;
            }
            div[data-testid="column"] {
                min-width: 100% !important;
                flex: 1 1 100% !important;
            }
            div[data-testid="stMetric"] {
                padding: 0.15rem 0;
            }
            .block-container {
                padding-top: 1rem;
                padding-left: 1rem;
                padding-right: 1rem;
                padding-bottom: 5rem;
            }
            .sticky-footer {
                font-size: 0.8rem;
                padding: 0.65rem 0.9rem;
            }
        }
        </style>
        <div class="app-header">
            <img class="app-header-logo" src="__LOGO__" alt="Logo OMNIS">
            <div class="app-header-text">
                <div class="main-title">Suivi Dynamique des Compagnies</div>
                <div class="sub-title">
                    Tableau de bord de pilotage pour suivre les projets, les objectifs, les investissements
                    et les mises a jour d'avancement.
                </div>
            </div>
        </div>
    """
    st.markdown(
        header_html.replace("__LOGO__", logo_src),
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="sticky-footer">
            Outils cree par RANAIVOSOA Tojoarimanana Hiratriniala : Tel +261 33 51 880 19
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metrics(df: pd.DataFrame, updates_df: pd.DataFrame) -> None:
    total_projects = len(df)
    total_sectors = df["secteur"].dropna().nunique()
    total_investment = df["investissement_initial_musd"].fillna(0).sum()
    projects_with_updates = df[df["nb_mises_a_jour"] > 0]["compagnie_projet"].nunique()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Nombre de projets", total_projects)
    col2.metric("Secteurs couverts", total_sectors)
    col3.metric("Investissement total", f"{total_investment:,.0f} M USD")
    col4.metric("Projets avec suivi", projects_with_updates)

    if not updates_df.empty:
        st.caption(
            f"{len(updates_df)} mises a jour detectees dans le fichier source."
        )


def render_dashboard(df: pd.DataFrame, updates_df: pd.DataFrame) -> None:
    st.markdown("### Vue d'ensemble")
    sector_summary = (
        df.groupby("secteur", dropna=False)["compagnie_projet"]
        .count()
        .rename("Nombre de projets")
        .reset_index()
        .fillna({"secteur": "Non renseigne"})
        .sort_values("Nombre de projets", ascending=False)
    )
    st.bar_chart(sector_summary.set_index("secteur"))

    resource_summary = (
        df.groupby("ressource_cible", dropna=False)["compagnie_projet"]
        .count()
        .rename("Nombre de projets")
        .reset_index()
        .fillna({"ressource_cible": "Non renseignee"})
        .sort_values("Nombre de projets", ascending=False)
    )
    st.markdown("### Repartition par ressource")
    st.dataframe(resource_summary, use_container_width=True, hide_index=True)

    st.markdown("### Dernieres mises a jour")
    if updates_df.empty:
        st.info("Aucune mise a jour detaillee n'a ete detectee dans le fichier.")
    else:
        latest_updates = updates_df.copy()
        latest_updates = latest_updates.sort_values(
            by=["date_mise_a_jour", "ordre_mise_a_jour"],
            ascending=[False, False],
            na_position="last",
        )
        latest_updates["date_mise_a_jour"] = latest_updates["date_mise_a_jour"].apply(
            format_date
        )
        st.dataframe(
            latest_updates.head(10)[["compagnie_projet", "date_mise_a_jour", "activite"]],
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("### Priorites d'action")
    priorities = df[
        [
            "compagnie_projet",
            "objectif",
            "travaux_proposes",
            "statut_actuel",
        ]
    ].copy()
    st.dataframe(priorities, use_container_width=True, hide_index=True)


def render_projects_table(df: pd.DataFrame) -> None:
    st.markdown("### Base projets")
    display_df = df[
        [
            "compagnie_projet",
            "secteur",
            "ressource_cible",
            "objectif",
            "zone_interet",
            "date_reference",
            "investissement_initial_musd",
            "dossier_soumis",
            "travaux_proposes",
            "statut_actuel",
            "nb_mises_a_jour",
        ]
    ].copy()
    display_df["date_reference"] = display_df["date_reference"].apply(format_date)
    display_df = display_df.rename(columns=DISPLAY_LABELS)
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.download_button(
        "Telecharger les donnees filtrees (CSV)",
        data=to_downloadable_excel(display_df),
        file_name="suivi_compagnies_filtre.csv",
        mime="text/csv",
    )


def render_timeline(df: pd.DataFrame, updates_df: pd.DataFrame) -> None:
    st.markdown("### Timeline de suivi")
    if df.empty:
        st.info("Aucun projet disponible avec les filtres actuels.")
        return

    company = st.selectbox(
        "Choisir une compagnie / un projet",
        df["compagnie_projet"].dropna().unique().tolist(),
    )

    project_info = df[df["compagnie_projet"] == company].iloc[0]
    st.markdown(
        f"""
        <div class="card">
            <strong>{project_info['compagnie_projet']}</strong><br>
            Secteur: {project_info.get('secteur') or '-'}<br>
            Ressource: {project_info.get('ressource_cible') or '-'}<br>
            Objectif: {project_info.get('objectif') or '-'}<br>
            Zone d'interet: {project_info.get('zone_interet') or '-'}<br>
            Statut actuel: {project_info.get('statut_actuel') or '-'}
        </div>
        """,
        unsafe_allow_html=True,
    )

    project_updates = updates_df[updates_df["compagnie_projet"] == company].copy()
    if project_updates.empty:
        st.warning("Aucune mise a jour detaillee pour ce projet.")
        return

    project_updates["date_mise_a_jour"] = project_updates["date_mise_a_jour"].apply(format_date)
    st.dataframe(
        project_updates[["ordre_mise_a_jour", "date_mise_a_jour", "activite"]]
        .rename(
            columns={
                "ordre_mise_a_jour": "Etape",
                "date_mise_a_jour": "Date",
                "activite": "Activite",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )


def main() -> None:
    render_header()

    uploaded_file = st.file_uploader(
        "Importer le fichier Excel de base",
        type=["xlsx", "xlsm", "xls"],
        help="Charge ton fichier source pour generer automatiquement le tableau de bord de suivi.",
    )

    if uploaded_file is None:
        st.info(
            "Veuillez importer votre fichier Excel pour ouvrir le suivi dynamique des compagnies."
        )
        st.stop()

    try:
        workbook_data = load_workbook(uploaded_file)
    except Exception as exc:
        st.error(f"Impossible de lire le fichier Excel: {exc}")
        st.stop()

    st.success(f"Fichier charge avec succes depuis l'onglet: {workbook_data.source_sheet}")

    filtered_projects = filter_dataframe(workbook_data.projects)
    if filtered_projects.empty:
        st.warning("Aucun resultat ne correspond aux filtres selectionnes.")
        st.stop()

    filtered_updates = workbook_data.updates[
        workbook_data.updates["compagnie_projet"].isin(filtered_projects["compagnie_projet"])
    ].copy()

    render_metrics(filtered_projects, filtered_updates)

    tab1, tab2, tab3 = st.tabs(
        ["Tableau de bord", "Table des projets", "Timeline par projet"]
    )

    with tab1:
        render_dashboard(filtered_projects, filtered_updates)

    with tab2:
        render_projects_table(filtered_projects)

    with tab3:
        render_timeline(filtered_projects, filtered_updates)


if __name__ == "__main__":
    main()
