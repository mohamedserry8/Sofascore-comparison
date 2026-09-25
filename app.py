import streamlit as st
import pandas as pd
import requests
import json
from datetime import datetime, timedelta
from rapidfuzz import fuzz, process
import io
import time

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Match Comparator",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0f1117; }
[data-testid="stSidebar"] { background: #161b27; border-right: 1px solid #2a3147; }
.stMetric { background: #161b27; border: 1px solid #2a3147; border-radius: 8px; padding: 12px; }
.status-missing  { color: #ef4444; font-weight: 600; }
.status-extra    { color: #f59e0b; font-weight: 600; }
.status-timemiss { color: #a78bfa; font-weight: 600; }
.status-ok       { color: #22c55e; font-weight: 600; }
div[data-testid="stDataFrameResizable"] { border: 1px solid #2a3147; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ─── Constants ────────────────────────────────────────────────────────────────
SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Cache-Control": "no-cache",
}

SOFASCORE_BASE = "https://api.sofascore.com/api/v1"

# Known name mappings: DB name → SofaScore name
DEFAULT_MAPPINGS = {
    "Côte d'Ivoire":          "Ivory Coast",
    "Congo DR":               "DR Congo",
    "Korea Republic":         "South Korea",
    "Korea DPR":              "North Korea",
    "São Tomé e Príncipe":    "Sao Tome and Principe",
    "Bosnia & Herzegovina":   "Bosnia and Herzegovina",
    "Chinese Taipei":         "Taiwan",
    "Kyrgyz Republic":        "Kyrgyzstan",
    "Brazil - Seria A":       "Brasileirão Série A",
    "Turkey Superliga":       "Süper Lig",
    "Champions League":       "UEFA Champions League",
    "PRIMERA DIVISIÓN":       "Primera División",
    "Stoiximan Super League": "Super League",
    "Denmark Superliga":      "Superliga",
    "Switzerland Super League": "Super League",
    "Chance Liga":            "Czech First League",
}

# ─── SofaScore fetcher ────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)  # cache 5 min
def fetch_sofascore_day(date_str: str) -> list[dict]:
    """Fetch all football events for a given date from SofaScore."""
    url = f"{SOFASCORE_BASE}/sport/football/scheduled-events/{date_str}"
    try:
        resp = requests.get(url, headers=SOFASCORE_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("events", [])
        rows = []
        for e in events:
            try:
                home = e.get("homeTeam", {}).get("name", "")
                away = e.get("awayTeam", {}).get("name", "")
                tournament = e.get("tournament", {}).get("name", "")
                category = e.get("tournament", {}).get("category", {}).get("name", "")
                ts = e.get("startTimestamp", 0)
                kickoff = datetime.utcfromtimestamp(ts).strftime("%H:%M") if ts else ""
                status = e.get("status", {}).get("description", "")
                sofascore_id = e.get("id", "")
                rows.append({
                    "sofascore_id": sofascore_id,
                    "home_team": home,
                    "away_team": away,
                    "tournament": tournament,
                    "category": category,
                    "match_date": date_str,
                    "kick_off_time": kickoff,
                    "status": status,
                })
            except Exception:
                continue
        return rows
    except requests.exceptions.RequestException as e:
        return []


def fetch_sofascore_range(date_from: datetime, date_to: datetime) -> pd.DataFrame:
    """Fetch SofaScore data for a date range."""
    all_rows = []
    current = date_from
    progress = st.progress(0, text="جاري جلب بيانات SofaScore...")
    total_days = (date_to - date_from).days + 1

    for i in range(total_days):
        date_str = current.strftime("%Y-%m-%d")
        progress.progress((i + 1) / total_days, text=f"جلب {date_str}...")
        rows = fetch_sofascore_day(date_str)
        all_rows.extend(rows)
        current += timedelta(days=1)
        time.sleep(0.3)  # be polite to the API

    progress.empty()
    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows)


# ─── Name normalizer ──────────────────────────────────────────────────────────
def normalize_name(name: str, mappings: dict) -> str:
    """Apply manual mappings then lowercase+strip for fuzzy."""
    n = str(name).strip()
    if n in mappings:
        n = mappings[n]
    return n.lower().strip()


# ─── Comparison engine ────────────────────────────────────────────────────────
def compare(db_df: pd.DataFrame, sf_df: pd.DataFrame,
            competition_filter: list[str],
            fuzzy_threshold: int,
            mappings: dict,
            exclude_cancelled: bool) -> pd.DataFrame:
    """
    Core comparison logic.
    Returns a DataFrame with columns:
        status, match_date, competition, home_team, away_team,
        db_kickoff, sf_kickoff, time_diff_min, match_score,
        db_id, home_team_id, away_team_id, competition_id,
        sf_home, sf_away, sf_tournament
    """

    # Filter DB by selected competitions
    if competition_filter:
        db_df = db_df[db_df["competition"].isin(competition_filter)]

    # Filter out cancelled
    if exclude_cancelled:
        db_df = db_df[db_df.get("match_play_status", pd.Series(["Normal"] * len(db_df))).str.lower() != "cancelled"]

    results = []
    sf_matched_idx = set()

    # Build a lookup: (date, norm_home, norm_away) → sf row index
    sf_lookup = {}
    for idx, row in sf_df.iterrows():
        key = (
            row["match_date"],
            normalize_name(row["home_team"], {}),
            normalize_name(row["away_team"], {}),
        )
        sf_lookup.setdefault(key, []).append(idx)

    # For each DB match
    for _, db_row in db_df.iterrows():
        db_date = str(db_row.get("match_date", ""))
        db_home = str(db_row.get("home_team", ""))
        db_away = str(db_row.get("away_team", ""))
        db_comp = str(db_row.get("competition", ""))
        db_kick = str(db_row.get("kick_off_time", ""))[:5]

        # Apply mappings
        db_home_n = normalize_name(db_home, mappings)
        db_away_n = normalize_name(db_away, mappings)

        # Candidates: same date ± 1 day
        candidate_dates = [db_date]
        try:
            d = datetime.strptime(db_date, "%Y-%m-%d")
            candidate_dates = [
                (d - timedelta(days=1)).strftime("%Y-%m-%d"),
                db_date,
                (d + timedelta(days=1)).strftime("%Y-%m-%d"),
            ]
        except Exception:
            pass

        best_score = 0
        best_idx = None

        # Filter candidates
        sf_candidates = sf_df[sf_df["match_date"].isin(candidate_dates)]

        for sf_idx, sf_row in sf_candidates.iterrows():
            sf_home_n = normalize_name(sf_row["home_team"], {})
            sf_away_n = normalize_name(sf_row["away_team"], {})
            home_score = fuzz.token_sort_ratio(db_home_n, sf_home_n)
            away_score = fuzz.token_sort_ratio(db_away_n, sf_away_n)
            avg = (home_score + away_score) / 2
            if avg > best_score:
                best_score = avg
                best_idx = sf_idx

        if best_idx is not None and best_score >= fuzzy_threshold:
            sf_row = sf_df.loc[best_idx]
            sf_matched_idx.add(best_idx)
            sf_kick = str(sf_row.get("kick_off_time", ""))[:5]

            # Calculate time diff
            time_diff = None
            try:
                if db_kick and sf_kick:
                    db_min = int(db_kick[:2]) * 60 + int(db_kick[3:5])
                    sf_min = int(sf_kick[:2]) * 60 + int(sf_kick[3:5])
                    time_diff = sf_min - db_min
            except Exception:
                pass

            if time_diff is not None and abs(time_diff) > 2:
                status = "⏱ فرق كيك أوف"
            else:
                status = "✓ متطابق"

            results.append({
                "status": status,
                "match_date": db_date,
                "competition": db_comp,
                "home_team": db_home,
                "away_team": db_away,
                "db_kickoff": db_kick,
                "sf_kickoff": sf_kick,
                "time_diff_min": time_diff,
                "match_score": round(best_score),
                "db_id": db_row.get("id", ""),
                "home_team_id": db_row.get("home_team_id", ""),
                "away_team_id": db_row.get("away_team_id", ""),
                "competition_id": db_row.get("competition_id", ""),
                "sf_home": sf_row["home_team"],
                "sf_away": sf_row["away_team"],
                "sf_tournament": sf_row["tournament"],
            })
        else:
            # In DB but not found in SofaScore
            results.append({
                "status": "🟡 مش في SofaScore",
                "match_date": db_date,
                "competition": db_comp,
                "home_team": db_home,
                "away_team": db_away,
                "db_kickoff": db_kick,
                "sf_kickoff": "",
                "time_diff_min": None,
                "match_score": round(best_score),
                "db_id": db_row.get("id", ""),
                "home_team_id": db_row.get("home_team_id", ""),
                "away_team_id": db_row.get("away_team_id", ""),
                "competition_id": db_row.get("competition_id", ""),
                "sf_home": "",
                "sf_away": "",
                "sf_tournament": "",
            })

    # SofaScore matches not matched to any DB row (missing from DB)
    for sf_idx, sf_row in sf_df.iterrows():
        if sf_idx in sf_matched_idx:
            continue
        # Only flag if the tournament name matches something in our competition list
        sf_tourn = str(sf_row.get("tournament", ""))
        # Check if it looks like something we track
        if competition_filter:
            best_comp_match = process.extractOne(
                sf_tourn.lower(),
                [c.lower() for c in competition_filter],
                scorer=fuzz.token_sort_ratio,
            )
            if best_comp_match is None or best_comp_match[1] < 60:
                continue  # Skip tournaments we don't track

        results.append({
            "status": "🔴 ناقص في DB",
            "match_date": sf_row["match_date"],
            "competition": sf_tourn,
            "home_team": sf_row["home_team"],
            "away_team": sf_row["away_team"],
            "db_kickoff": "",
            "sf_kickoff": str(sf_row.get("kick_off_time", ""))[:5],
            "time_diff_min": None,
            "match_score": 0,
            "db_id": "",
            "home_team_id": "",
            "away_team_id": "",
            "competition_id": "",
            "sf_home": sf_row["home_team"],
            "sf_away": sf_row["away_team"],
            "sf_tournament": sf_tourn,
        })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


# ─── UI ───────────────────────────────────────────────────────────────────────
st.title("⚽ Match Data Comparator")
st.caption("قارن بياناتك مع SofaScore — اكتشف الماتشات الناقصة وفروق المواعيد")

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ الإعدادات")

    # Date range
    st.subheader("📅 نطاق التاريخ")
    today = datetime.today().date()
    col1, col2 = st.columns(2)
    with col1:
        date_from = st.date_input("من", value=today)
    with col2:
        date_to = st.date_input("إلى", value=today + timedelta(days=7))

    preset = st.radio("اختصارات", ["اليوم", "اليوم + بكرة", "أسبوع قادم", "مخصص"], index=2, horizontal=True)
    if preset == "اليوم":
        date_from = date_to = today
    elif preset == "اليوم + بكرة":
        date_from = today
        date_to = today + timedelta(days=1)
    elif preset == "أسبوع قادم":
        date_from = today
        date_to = today + timedelta(days=7)

    st.divider()

    # Fuzzy threshold
    st.subheader("🎯 حساسية المطابقة")
    fuzzy_threshold = st.slider(
        "نسبة الـ Fuzzy Match",
        min_value=50, max_value=100, value=78, step=1,
        help="كلما زاد الرقم، كلما احتجت تطابق أدق في أسماء الفرق"
    )

    st.divider()

    # Options
    st.subheader("🔧 خيارات")
    exclude_cancelled = st.checkbox("استبعاد Cancelled من بياناتك", value=True)
    show_matched = st.checkbox("إظهار الماتشات المتطابقة", value=False)

    st.divider()
    st.caption("💡 بعد رفع الملف، اختار البطولات واضغط تشغيل")

# ── Main area ─────────────────────────────────────────────────────────────────
tab_main, tab_mapping, tab_help = st.tabs(["📊 المقارنة", "🔗 Name Mapping", "❓ مساعدة"])

# ── Tab: Mapping ─────────────────────────────────────────────────────────────
with tab_mapping:
    st.subheader("جدول تعيين أسماء الفرق والبطولات")
    st.caption("لو اسم فريق في بياناتك مختلف عن اسمه على SofaScore، عرّفه هنا")

    if "mappings" not in st.session_state:
        st.session_state.mappings = DEFAULT_MAPPINGS.copy()

    # Edit as dataframe
    mapping_df = pd.DataFrame(
        [{"DB Name": k, "SofaScore Name": v} for k, v in st.session_state.mappings.items()]
    )
    edited = st.data_editor(
        mapping_df,
        num_rows="dynamic",
        use_container_width=True,
        key="mapping_editor"
    )

    if st.button("💾 حفظ الـ Mappings"):
        st.session_state.mappings = {
            row["DB Name"]: row["SofaScore Name"]
            for _, row in edited.iterrows()
            if row["DB Name"] and row["SofaScore Name"]
        }
        st.success(f"✅ تم حفظ {len(st.session_state.mappings)} mapping")

# ── Tab: Help ─────────────────────────────────────────────────────────────────
with tab_help:
    st.subheader("كيفية الاستخدام")
    st.markdown("""
    **الخطوة 1 — جهز الـ CSV من DBeaver**

    استخدم الكويري دي (مع إضافة الـ IDs):
    ```sql
    SELECT 
        m.id,
        c3.id AS competition_id,
        c3.name AS competition,
        c4.name AS competition_country,
        s.name AS season,
        t.id AS home_team_id,
        t.name AS home_team,
        t2.id AS away_team_id,
        t2.name AS away_team,
        m.match_date,
        m.kick_off_time,
        m.match_play_status
    FROM matches m
    LEFT JOIN teams t ON m.home_team_id = t.id
    LEFT JOIN teams t2 ON m.away_team_id = t2.id
    LEFT JOIN competition_season cs ON m.competition_season_id = cs.id
    LEFT JOIN competitions c3 ON cs.competition_id = c3.id
    LEFT JOIN countries c4 ON c3.country_id = c4.id
    LEFT JOIN season s ON cs.season_id = s.id
    WHERE m.match_name NOT LIKE '%review%' 
      AND s.id IN (351, 316, 355, 356)
    ORDER BY m.match_date, c3.name
    ```

    **الخطوة 2 — ارفع ملف البطولات (competitions CSV)**

    ملف فيه: competition_id, competition, competition_country, season

    **الخطوة 3 — ارفع ملف الماتشات (matches CSV)**

    **الخطوة 4 — اختار البطولات اللي عايز تقارنها**

    **الخطوة 5 — اضغط "تشغيل المقارنة"**

    ---
    **معنى الألوان:**
    - 🔴 **ناقص في DB** — موجود على SofaScore مش موجود عندك
    - 🟡 **مش في SofaScore** — موجود عندك مش لاقيه على SofaScore
    - ⏱ **فرق كيك أوف** — الماتش موجود بس الوقت مختلف
    - ✓ **متطابق** — تمام
    """)

# ── Tab: Main ─────────────────────────────────────────────────────────────────
with tab_main:
    col_upload1, col_upload2 = st.columns(2)

    with col_upload1:
        st.subheader("🗄️ ملف البطولات (competitions)")
        comp_file = st.file_uploader(
            "competition_id, competition, competition_country, season",
            type=["csv"],
            key="comp_upload"
        )

    with col_upload2:
        st.subheader("📋 ملف الماتشات (matches)")
        match_file = st.file_uploader(
            "id, competition, home_team, away_team, match_date, kick_off_time ...",
            type=["csv"],
            key="match_upload"
        )

    # Load files
    comp_df = None
    db_df = None

    if comp_file:
        try:
            comp_df = pd.read_csv(comp_file)
            comp_df.columns = [c.strip().lower() for c in comp_df.columns]
            st.success(f"✅ البطولات: {len(comp_df)} بطولة")
        except Exception as e:
            st.error(f"خطأ في قراءة ملف البطولات: {e}")

    if match_file:
        try:
            db_df = pd.read_csv(match_file)
            db_df.columns = [c.strip().lower() for c in db_df.columns]
            db_df["match_date"] = pd.to_datetime(db_df["match_date"]).dt.strftime("%Y-%m-%d")
            st.success(f"✅ الماتشات: {len(db_df):,} ماتش")
        except Exception as e:
            st.error(f"خطأ في قراءة ملف الماتشات: {e}")

    st.divider()

    # Competition filter
    if comp_df is not None and db_df is not None:
        # Get competitions from CSV
        if "competition" in comp_df.columns:
            all_comps = sorted(comp_df["competition"].dropna().unique().tolist())
        else:
            all_comps = sorted(db_df["competition"].dropna().unique().tolist())

        st.subheader("🏆 اختار البطولات")

        col_sel1, col_sel2, col_sel3 = st.columns([2, 1, 1])
        with col_sel1:
            country_filter = st.multiselect(
                "فلتر بالدولة/القارة",
                options=sorted(comp_df["competition_country"].dropna().unique().tolist()) if comp_df is not None and "competition_country" in comp_df.columns else [],
                placeholder="كل الدول"
            )

        with col_sel2:
            select_all = st.button("✅ تحديد الكل")
        with col_sel3:
            clear_all = st.button("❌ مسح الكل")

        # Filter comps by country
        if country_filter and comp_df is not None and "competition_country" in comp_df.columns:
            filtered_comps = comp_df[comp_df["competition_country"].isin(country_filter)]["competition"].dropna().unique().tolist()
        else:
            filtered_comps = all_comps

        if "selected_comps" not in st.session_state:
            st.session_state.selected_comps = filtered_comps[:20]  # default first 20

        if select_all:
            st.session_state.selected_comps = filtered_comps
        if clear_all:
            st.session_state.selected_comps = []

        selected_comps = st.multiselect(
            f"البطولات المحددة ({len(filtered_comps)} متاحة)",
            options=filtered_comps,
            default=st.session_state.selected_comps,
            key="comp_select"
        )
        st.session_state.selected_comps = selected_comps

        st.caption(f"محدد: {len(selected_comps)} بطولة")

        st.divider()

        # Run button
        col_run, col_info = st.columns([1, 3])
        with col_run:
            run_btn = st.button(
                "▶ تشغيل المقارنة",
                type="primary",
                disabled=not selected_comps,
                use_container_width=True
            )
        with col_info:
            days_count = (date_to - date_from).days + 1
            st.info(f"📅 من {date_from} إلى {date_to} ({days_count} يوم) | 🏆 {len(selected_comps)} بطولة | 🎯 Fuzzy {fuzzy_threshold}%")

        if run_btn:
            with st.spinner("⏳ جاري المعالجة..."):
                # Step 1: Fetch from SofaScore
                st.info("1️⃣ جلب بيانات SofaScore...")
                sf_df = fetch_sofascore_range(
                    datetime.combine(date_from, datetime.min.time()),
                    datetime.combine(date_to, datetime.min.time()),
                )

                if sf_df.empty:
                    st.error("❌ تعذّر جلب البيانات من SofaScore. ربما API محجوب أو فيه مشكلة في الاتصال.")
                    st.stop()

                st.info(f"2️⃣ جلب {len(sf_df):,} ماتش من SofaScore — جاري المقارنة...")

                # Step 2: Filter DB by date
                db_filtered = db_df[
                    (db_df["match_date"] >= date_from.strftime("%Y-%m-%d")) &
                    (db_df["match_date"] <= date_to.strftime("%Y-%m-%d"))
                ].copy()

                # Step 3: Compare
                mappings = st.session_state.get("mappings", DEFAULT_MAPPINGS)
                result_df = compare(
                    db_filtered, sf_df,
                    competition_filter=selected_comps,
                    fuzzy_threshold=fuzzy_threshold,
                    mappings=mappings,
                    exclude_cancelled=exclude_cancelled,
                )

                st.session_state.result_df = result_df
                st.session_state.sf_df = sf_df

        # ── Show results ───────────────────────────────────────────────────────
        if "result_df" in st.session_state and not st.session_state.result_df.empty:
            result_df = st.session_state.result_df

            # Stats
            counts = result_df["status"].value_counts()
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                n = counts.get("🔴 ناقص في DB", 0)
                st.metric("🔴 ناقص في DB", n, help="موجود على SofaScore مش موجود عندك")
            with c2:
                n = counts.get("🟡 مش في SofaScore", 0)
                st.metric("🟡 مش في SofaScore", n, help="موجود عندك مش لاقيه على SofaScore")
            with c3:
                n = counts.get("⏱ فرق كيك أوف", 0)
                st.metric("⏱ فرق كيك أوف", n, help="الماتش موجود بس الوقت مختلف")
            with c4:
                n = counts.get("✓ متطابق", 0)
                st.metric("✓ متطابق", n)

            st.divider()

            # Filter UI
            col_f1, col_f2, col_f3, col_f4 = st.columns([2, 2, 2, 1])
            with col_f1:
                search_term = st.text_input("🔍 بحث بالفريق", placeholder="اسم الفريق...")
            with col_f2:
                status_filter = st.multiselect(
                    "الحالة",
                    options=result_df["status"].unique().tolist(),
                    default=[s for s in result_df["status"].unique() if s != "✓ متطابق"]
                )
            with col_f3:
                comp_result_filter = st.multiselect(
                    "البطولة",
                    options=sorted(result_df["competition"].unique().tolist()),
                    placeholder="كل البطولات"
                )
            with col_f4:
                date_result_filter = st.selectbox(
                    "التاريخ",
                    options=["الكل"] + sorted(result_df["match_date"].unique().tolist())
                )

            # Apply filters
            display_df = result_df.copy()
            if status_filter:
                display_df = display_df[display_df["status"].isin(status_filter)]
            if comp_result_filter:
                display_df = display_df[display_df["competition"].isin(comp_result_filter)]
            if date_result_filter != "الكل":
                display_df = display_df[display_df["match_date"] == date_result_filter]
            if search_term:
                mask = (
                    display_df["home_team"].str.contains(search_term, case=False, na=False) |
                    display_df["away_team"].str.contains(search_term, case=False, na=False)
                )
                display_df = display_df[mask]

            # Hide matched if not requested
            if not show_matched:
                display_df = display_df[display_df["status"] != "✓ متطابق"]

            st.caption(f"عرض {len(display_df):,} من {len(result_df):,} نتيجة")

            # Display table
            st.dataframe(
                display_df[[
                    "status", "match_date", "competition",
                    "home_team", "away_team",
                    "db_kickoff", "sf_kickoff", "time_diff_min",
                    "match_score", "db_id"
                ]].rename(columns={
                    "status": "الحالة",
                    "match_date": "التاريخ",
                    "competition": "البطولة",
                    "home_team": "الهوم",
                    "away_team": "الأواي",
                    "db_kickoff": "كيك أوف (DB)",
                    "sf_kickoff": "كيك أوف (SofaScore)",
                    "time_diff_min": "الفرق (دقيقة)",
                    "match_score": "نسبة التطابق %",
                    "db_id": "DB ID",
                }),
                use_container_width=True,
                height=500,
            )

            # Export
            st.divider()
            col_exp1, col_exp2 = st.columns(2)

            with col_exp1:
                csv_export = display_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "⬇️ Export نتائج الفلتر (CSV)",
                    data=csv_export,
                    file_name=f"comparison_{date_from}_{date_to}.csv",
                    mime="text/csv",
                )

            with col_exp2:
                # Export only problems
                problems_df = result_df[result_df["status"] != "✓ متطابق"]
                csv_problems = problems_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "🚨 Export المشاكل فقط (CSV)",
                    data=csv_problems,
                    file_name=f"problems_{date_from}_{date_to}.csv",
                    mime="text/csv",
                )

            # ── SofaScore raw data viewer ──────────────────────────────────
            with st.expander("👁️ عرض بيانات SofaScore الخام"):
                sf_display = st.session_state.sf_df
                sf_search = st.text_input("بحث في SofaScore data", key="sf_search")
                if sf_search:
                    mask = (
                        sf_display["home_team"].str.contains(sf_search, case=False, na=False) |
                        sf_display["away_team"].str.contains(sf_search, case=False, na=False) |
                        sf_display["tournament"].str.contains(sf_search, case=False, na=False)
                    )
                    sf_display = sf_display[mask]
                st.dataframe(sf_display, use_container_width=True, height=300)

    elif comp_file is None or match_file is None:
        st.info("⬆️ ارفع ملف البطولات وملف الماتشات عشان تبدأ")
