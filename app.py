import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
from rapidfuzz import fuzz, process
import time
import random

st.set_page_config(
    page_title="Match Comparator ⚽",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0f1117; }
[data-testid="stSidebar"] { background: #161b27; border-right: 1px solid #2a3147; }
div[data-testid="stDataFrameResizable"] { border: 1px solid #2a3147; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ─── Headers pool — rotate to avoid blocks ───────────────────────────────────
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

def get_headers():
    return {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
    }

SOFASCORE_BASE = "https://api.sofascore.com/api/v1"

DEFAULT_MAPPINGS = {
    "Côte d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo",
    "Korea Republic": "South Korea",
    "Korea DPR": "North Korea",
    "São Tomé e Príncipe": "Sao Tome and Principe",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Chinese Taipei": "Taiwan",
    "Kyrgyz Republic": "Kyrgyzstan",
    "Brazil - Seria A": "Brasileirão Série A",
    "Turkey Superliga": "Süper Lig",
    "Champions League": "UEFA Champions League",
    "PRIMERA DIVISIÓN": "Primera División",
    "Stoiximan Super League": "Super League",
    "Denmark Superliga": "Superliga",
    "Switzerland Super League": "Super League",
    "Chance Liga": "Czech First League",
}

# ─── SofaScore fetcher with retry ────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def fetch_sofascore_day(date_str: str) -> tuple[list, str]:
    """Returns (rows, error_msg). error_msg is empty string on success."""
    url = f"{SOFASCORE_BASE}/sport/football/scheduled-events/{date_str}"

    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(1.5 * attempt)
            resp = requests.get(url, headers=get_headers(), timeout=20)

            if resp.status_code == 403:
                return [], "403_blocked"
            if resp.status_code == 429:
                time.sleep(3)
                continue
            resp.raise_for_status()

            data = resp.json()
            events = data.get("events", [])
            rows = []
            for e in events:
                try:
                    ts = e.get("startTimestamp", 0)
                    kickoff = datetime.utcfromtimestamp(ts).strftime("%H:%M") if ts else ""
                    rows.append({
                        "sofascore_id": e.get("id", ""),
                        "home_team": e.get("homeTeam", {}).get("name", ""),
                        "away_team": e.get("awayTeam", {}).get("name", ""),
                        "tournament": e.get("tournament", {}).get("name", ""),
                        "category": e.get("tournament", {}).get("category", {}).get("name", ""),
                        "match_date": date_str,
                        "kick_off_time": kickoff,
                        "status": e.get("status", {}).get("description", ""),
                    })
                except Exception:
                    continue
            return rows, ""

        except requests.exceptions.ConnectionError:
            return [], "connection_error"
        except requests.exceptions.Timeout:
            if attempt == 2:
                return [], "timeout"
            continue
        except Exception as ex:
            return [], str(ex)

    return [], "max_retries"


def fetch_sofascore_range(date_from: datetime, date_to: datetime):
    """Returns (DataFrame, error_type). error_type: '' | '403_blocked' | other"""
    all_rows = []
    total_days = (date_to - date_from).days + 1
    progress = st.progress(0, text="جاري جلب بيانات SofaScore...")

    for i in range(total_days):
        current = date_from + timedelta(days=i)
        date_str = current.strftime("%Y-%m-%d")
        progress.progress((i + 1) / total_days, text=f"جلب {date_str}...")

        rows, err = fetch_sofascore_day(date_str)
        if err == "403_blocked":
            progress.empty()
            return pd.DataFrame(), "403_blocked"
        if err:
            progress.empty()
            return pd.DataFrame(), err

        all_rows.extend(rows)
        time.sleep(0.4 + random.uniform(0, 0.3))

    progress.empty()
    return (pd.DataFrame(all_rows) if all_rows else pd.DataFrame()), ""


# ─── Parse SofaScore CSV export ──────────────────────────────────────────────
def parse_sofascore_csv(uploaded_file) -> tuple[pd.DataFrame, str]:
    """
    Handle different SofaScore CSV/export formats.
    Returns (DataFrame with standard columns, error_msg)
    """
    try:
        df = pd.read_csv(uploaded_file)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        # Map common column name variants
        col_map = {
            # home team
            "home": "home_team", "home_team_name": "home_team", "hometeam": "home_team",
            "home team": "home_team", "local": "home_team",
            # away team
            "away": "away_team", "away_team_name": "away_team", "awayteam": "away_team",
            "away team": "away_team", "visitor": "away_team",
            # date
            "date": "match_date", "game_date": "match_date", "event_date": "match_date",
            "start_date": "match_date",
            # time
            "time": "kick_off_time", "kickoff": "kick_off_time", "start_time": "kick_off_time",
            "kick_off": "kick_off_time", "ko": "kick_off_time",
            # tournament
            "league": "tournament", "competition": "tournament",
            "tournament_name": "tournament", "league_name": "tournament",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        required = ["home_team", "away_team", "match_date"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            return pd.DataFrame(), f"الأعمدة دي مش موجودة: {', '.join(missing)}\nالأعمدة الموجودة: {', '.join(df.columns.tolist())}"

        # Normalize date
        df["match_date"] = pd.to_datetime(df["match_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        if "kick_off_time" not in df.columns:
            df["kick_off_time"] = ""
        if "tournament" not in df.columns:
            df["tournament"] = ""

        df["kick_off_time"] = df["kick_off_time"].astype(str).str[:5]
        return df, ""

    except Exception as ex:
        return pd.DataFrame(), str(ex)


# ─── Comparison engine ────────────────────────────────────────────────────────
def normalize(name: str, mappings: dict) -> str:
    n = str(name).strip()
    n = mappings.get(n, n)
    return n.lower().strip()


def compare(db_df: pd.DataFrame, sf_df: pd.DataFrame,
            competition_filter: list, fuzzy_threshold: int,
            mappings: dict, exclude_cancelled: bool) -> pd.DataFrame:

    if competition_filter:
        db_df = db_df[db_df["competition"].isin(competition_filter)].copy()
    if exclude_cancelled and "match_play_status" in db_df.columns:
        db_df = db_df[db_df["match_play_status"].str.lower() != "cancelled"].copy()

    results = []
    sf_matched = set()

    for _, db in db_df.iterrows():
        db_date = str(db.get("match_date", ""))
        db_home = str(db.get("home_team", ""))
        db_away = str(db.get("away_team", ""))
        db_kick = str(db.get("kick_off_time", ""))[:5]

        db_home_n = normalize(db_home, mappings)
        db_away_n = normalize(db_away, mappings)

        # Candidate dates ±1
        try:
            d = datetime.strptime(db_date, "%Y-%m-%d")
            cand_dates = [(d + timedelta(days=x)).strftime("%Y-%m-%d") for x in (-1, 0, 1)]
        except Exception:
            cand_dates = [db_date]

        candidates = sf_df[sf_df["match_date"].isin(cand_dates)]
        best_score, best_idx = 0, None

        for idx, sf in candidates.iterrows():
            sf_home_n = normalize(str(sf["home_team"]), {})
            sf_away_n = normalize(str(sf["away_team"]), {})
            score = (fuzz.token_sort_ratio(db_home_n, sf_home_n) +
                     fuzz.token_sort_ratio(db_away_n, sf_away_n)) / 2
            if score > best_score:
                best_score, best_idx = score, idx

        if best_idx is not None and best_score >= fuzzy_threshold:
            sf = sf_df.loc[best_idx]
            sf_matched.add(best_idx)
            sf_kick = str(sf.get("kick_off_time", ""))[:5]

            time_diff = None
            try:
                if db_kick and sf_kick and len(db_kick) == 5 and len(sf_kick) == 5:
                    db_m = int(db_kick[:2]) * 60 + int(db_kick[3:])
                    sf_m = int(sf_kick[:2]) * 60 + int(sf_kick[3:])
                    time_diff = sf_m - db_m
            except Exception:
                pass

            status = "⏱ فرق كيك أوف" if (time_diff is not None and abs(time_diff) > 2) else "✓ متطابق"
            results.append({
                "status": status, "match_date": db_date,
                "competition": str(db.get("competition", "")),
                "home_team": db_home, "away_team": db_away,
                "db_kickoff": db_kick, "sf_kickoff": sf_kick,
                "time_diff_min": time_diff, "match_score": round(best_score),
                "db_id": db.get("id", ""), "home_team_id": db.get("home_team_id", ""),
                "away_team_id": db.get("away_team_id", ""), "competition_id": db.get("competition_id", ""),
                "sf_home": sf["home_team"], "sf_away": sf["away_team"],
                "sf_tournament": sf.get("tournament", ""),
            })
        else:
            results.append({
                "status": "🟡 مش في SofaScore", "match_date": db_date,
                "competition": str(db.get("competition", "")),
                "home_team": db_home, "away_team": db_away,
                "db_kickoff": db_kick, "sf_kickoff": "",
                "time_diff_min": None, "match_score": round(best_score),
                "db_id": db.get("id", ""), "home_team_id": db.get("home_team_id", ""),
                "away_team_id": db.get("away_team_id", ""), "competition_id": db.get("competition_id", ""),
                "sf_home": "", "sf_away": "", "sf_tournament": "",
            })

    # Matches in SofaScore not in DB
    for idx, sf in sf_df.iterrows():
        if idx in sf_matched:
            continue
        sf_tourn = str(sf.get("tournament", ""))
        if competition_filter:
            best = process.extractOne(sf_tourn.lower(),
                                      [c.lower() for c in competition_filter],
                                      scorer=fuzz.token_sort_ratio)
            if not best or best[1] < 60:
                continue
        results.append({
            "status": "🔴 ناقص في DB", "match_date": str(sf["match_date"]),
            "competition": sf_tourn,
            "home_team": str(sf["home_team"]), "away_team": str(sf["away_team"]),
            "db_kickoff": "", "sf_kickoff": str(sf.get("kick_off_time", ""))[:5],
            "time_diff_min": None, "match_score": 0,
            "db_id": "", "home_team_id": "", "away_team_id": "", "competition_id": "",
            "sf_home": str(sf["home_team"]), "sf_away": str(sf["away_team"]),
            "sf_tournament": sf_tourn,
        })

    return pd.DataFrame(results) if results else pd.DataFrame()


# ════════════════════════════════════════════════════════════════════════════════
# UI
# ════════════════════════════════════════════════════════════════════════════════
st.title("⚽ Match Data Comparator")
st.caption("قارن بياناتك مع SofaScore — اكتشف الماتشات الناقصة وفروق المواعيد")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ الإعدادات")

    today = datetime.today().date()
    st.subheader("📅 نطاق التاريخ")
    preset = st.radio("اختصارات", ["اليوم", "اليوم + بكرة", "أسبوع قادم", "مخصص"],
                      index=2, horizontal=True)
    if preset == "اليوم":
        date_from = date_to = today
    elif preset == "اليوم + بكرة":
        date_from, date_to = today, today + timedelta(days=1)
    elif preset == "أسبوع قادم":
        date_from, date_to = today, today + timedelta(days=7)
    else:
        c1, c2 = st.columns(2)
        date_from = c1.date_input("من", value=today)
        date_to = c2.date_input("إلى", value=today + timedelta(days=7))

    st.divider()
    st.subheader("🎯 حساسية المطابقة")
    fuzzy_threshold = st.slider("Fuzzy Match %", 50, 100, 78)

    st.divider()
    st.subheader("🔧 خيارات")
    exclude_cancelled = st.checkbox("استبعاد Cancelled", value=True)
    show_matched = st.checkbox("إظهار المتطابقات", value=False)

    st.divider()
    st.caption("💡 جيب بيانات SofaScore عن طريق الـ Tampermonkey script وارفعها في الصفحة الرئيسية")

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_main, tab_mapping, tab_help = st.tabs(["📊 المقارنة", "🔗 Name Mapping", "❓ مساعدة"])

with tab_mapping:
    st.subheader("جدول تعيين الأسماء")
    st.caption("لو اسم فريق في بياناتك مختلف عن اسمه على SofaScore")
    if "mappings" not in st.session_state:
        st.session_state.mappings = DEFAULT_MAPPINGS.copy()
    mdf = pd.DataFrame([{"DB Name": k, "SofaScore Name": v}
                        for k, v in st.session_state.mappings.items()])
    edited = st.data_editor(mdf, num_rows="dynamic", use_container_width=True)
    if st.button("💾 حفظ"):
        st.session_state.mappings = {
            r["DB Name"]: r["SofaScore Name"]
            for _, r in edited.iterrows() if r["DB Name"] and r["SofaScore Name"]
        }
        st.success(f"✅ تم حفظ {len(st.session_state.mappings)} mapping")

with tab_help:
    st.subheader("كيفية رفع CSV من SofaScore يدوياً")
    st.markdown("""
    لو الـ API اتبلوك، ممكن تجيب الداتا يدوياً بأي طريقة من دول:

    **طريقة 1 — SofaScore website مباشرة:**
    1. روح على sofascore.com
    2. اختار اليوم اللي عايزه
    3. في المتصفح: F12 → Network → ابحث عن `scheduled-events`
    4. افتح الـ request → Copy response
    5. احفظه كـ JSON وحوّله لـ CSV

    **طريقة 2 — استخدم أداة مجانية:**
    - [sofascore-api.vercel.app](https://sofascore-api.vercel.app) (غير رسمي)
    - أو أي SofaScore scraper على Apify (فيه free tier)

    **فورمات الـ CSV المطلوب (الأعمدة الأساسية):**
    ```
    home_team, away_team, match_date, kick_off_time, tournament
    Arsenal, Chelsea, 2026-09-25, 17:30, Premier League
    ```

    **الأعمدة البديلة المقبولة:**
    - home / home team / hometeam
    - away / away team / awayteam
    - date / game_date / event_date
    - time / kickoff / ko / start_time
    - league / competition / league_name
    """)

with tab_main:
    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("🗄️ بطولاتك")
        comp_file = st.file_uploader(
            "competitions CSV",
            type=["csv"], key="comp",
            help="competition_id, competition, competition_country, season"
        )

    with col2:
        st.subheader("📋 ماتشاتك (DB)")
        match_file = st.file_uploader(
            "matches CSV من DBeaver",
            type=["csv"], key="matches",
            help="id, competition, home_team, away_team, match_date, kick_off_time"
        )

    with col3:
        st.subheader("🌐 ماتشات SofaScore")
        sf_file = st.file_uploader(
            "CSV من Tampermonkey script",
            type=["csv"], key="sf_manual",
            help="sofascore_id, home_team, away_team, tournament, match_date, kick_off_time"
        )

    # ── Load files ────────────────────────────────────────────────────────────
    comp_df = db_df = sf_manual_df = None

    if comp_file:
        try:
            comp_df = pd.read_csv(comp_file)
            comp_df.columns = [c.strip().lower() for c in comp_df.columns]
            with col1:
                st.success(f"✅ {len(comp_df)} بطولة")
        except Exception as e:
            with col1:
                st.error(f"خطأ: {e}")

    if match_file:
        try:
            db_df = pd.read_csv(match_file)
            db_df.columns = [c.strip().lower() for c in db_df.columns]
            db_df["match_date"] = pd.to_datetime(db_df["match_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            with col2:
                st.success(f"✅ {len(db_df):,} ماتش")
        except Exception as e:
            with col2:
                st.error(f"خطأ: {e}")

    if sf_file:
        sf_manual_df, err = parse_sofascore_csv(sf_file)
        with col3:
            if err:
                st.error(f"❌ {err}")
            else:
                st.success(f"✅ {len(sf_manual_df):,} ماتش")
                with st.expander("معاينة SofaScore"):
                    st.dataframe(sf_manual_df.head(8), use_container_width=True)

    st.divider()

    if comp_df is not None and db_df is not None:
        # Competition selector
        all_comps = sorted(comp_df["competition"].dropna().unique().tolist()) \
            if "competition" in comp_df.columns \
            else sorted(db_df["competition"].dropna().unique().tolist())

        st.subheader("🏆 اختار البطولات")

        country_opts = []
        if comp_df is not None and "competition_country" in comp_df.columns:
            country_opts = sorted(comp_df["competition_country"].dropna().unique().tolist())

        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            country_filter = st.multiselect("فلتر بالدولة", options=country_opts, placeholder="كل الدول")
        with c2:
            sel_all = st.button("✅ الكل")
        with c3:
            clr_all = st.button("❌ مسح")

        filtered_comps = all_comps
        if country_filter and comp_df is not None and "competition_country" in comp_df.columns:
            filtered_comps = comp_df[comp_df["competition_country"].isin(country_filter)]["competition"].dropna().unique().tolist()

        if "selected_comps" not in st.session_state:
            st.session_state.selected_comps = filtered_comps[:20]
        if sel_all:
            st.session_state.selected_comps = filtered_comps
        if clr_all:
            st.session_state.selected_comps = []

        selected_comps = st.multiselect(
            f"البطولات ({len(filtered_comps)} متاحة)",
            options=filtered_comps,
            default=[c for c in st.session_state.selected_comps if c in filtered_comps],
            key="comp_sel"
        )
        st.session_state.selected_comps = selected_comps
        st.caption(f"محدد: {len(selected_comps)} بطولة")

        st.divider()

        days_n = (date_to - date_from).days + 1
        sf_ready = sf_manual_df is not None and not sf_manual_df.empty
        run_disabled = not selected_comps or not sf_ready

        c_run, c_info = st.columns([1, 3])
        with c_run:
            run_btn = st.button("▶ تشغيل المقارنة", type="primary",
                                disabled=run_disabled, use_container_width=True)
        with c_info:
            if not sf_ready:
                st.warning("⬆️ ارفع ملف SofaScore عشان تقدر تشغّل المقارنة")
            else:
                sf_count = len(sf_manual_df)
                st.info(f"📅 {date_from} ← {date_to} ({days_n} يوم) | 🏆 {len(selected_comps)} بطولة | 🌐 {sf_count:,} ماتش SofaScore")

        if run_btn:
            sf_df = sf_manual_df.copy()

            if sf_df.empty:
                st.warning("⚠️ مفيش بيانات SofaScore")
                st.stop()

            st.info(f"📊 {len(sf_df):,} ماتش من SofaScore — جاري المقارنة...")

            db_filtered = db_df[
                (db_df["match_date"] >= date_from.strftime("%Y-%m-%d")) &
                (db_df["match_date"] <= date_to.strftime("%Y-%m-%d"))
            ].copy()

            with st.spinner("جاري المقارنة..."):
                result_df = compare(
                    db_filtered, sf_df, selected_comps,
                    fuzzy_threshold,
                    st.session_state.get("mappings", DEFAULT_MAPPINGS),
                    exclude_cancelled,
                )

            st.session_state.result_df = result_df
            st.session_state.sf_df = sf_df

        # ── Results ───────────────────────────────────────────────────────────
        if "result_df" in st.session_state and not st.session_state.result_df.empty:
            result_df = st.session_state.result_df
            counts = result_df["status"].value_counts()

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("🔴 ناقص في DB", counts.get("🔴 ناقص في DB", 0))
            c2.metric("🟡 مش في SofaScore", counts.get("🟡 مش في SofaScore", 0))
            c3.metric("⏱ فرق كيك أوف", counts.get("⏱ فرق كيك أوف", 0))
            c4.metric("✓ متطابق", counts.get("✓ متطابق", 0))

            st.divider()

            f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
            search = f1.text_input("🔍 بحث بالفريق", placeholder="اسم الفريق...")
            status_f = f2.multiselect("الحالة", result_df["status"].unique().tolist(),
                                      default=[s for s in result_df["status"].unique() if s != "✓ متطابق"])
            comp_f = f3.multiselect("البطولة", sorted(result_df["competition"].unique().tolist()))
            date_f = f4.selectbox("التاريخ", ["الكل"] + sorted(result_df["match_date"].unique().tolist()))

            disp = result_df.copy()
            if status_f:
                disp = disp[disp["status"].isin(status_f)]
            if comp_f:
                disp = disp[disp["competition"].isin(comp_f)]
            if date_f != "الكل":
                disp = disp[disp["match_date"] == date_f]
            if search:
                mask = (disp["home_team"].str.contains(search, case=False, na=False) |
                        disp["away_team"].str.contains(search, case=False, na=False))
                disp = disp[mask]
            if not show_matched:
                disp = disp[disp["status"] != "✓ متطابق"]

            st.caption(f"عرض {len(disp):,} من {len(result_df):,} نتيجة")

            st.dataframe(
                disp[["status", "match_date", "competition", "home_team", "away_team",
                       "db_kickoff", "sf_kickoff", "time_diff_min", "match_score", "db_id"]].rename(columns={
                    "status": "الحالة", "match_date": "التاريخ", "competition": "البطولة",
                    "home_team": "الهوم", "away_team": "الأواي",
                    "db_kickoff": "كيك أوف DB", "sf_kickoff": "كيك أوف SofaScore",
                    "time_diff_min": "فرق (دقيقة)", "match_score": "تطابق %", "db_id": "DB ID",
                }),
                use_container_width=True, height=480,
            )

            st.divider()
            ec1, ec2 = st.columns(2)
            ec1.download_button("⬇️ Export نتائج الفلتر",
                                disp.to_csv(index=False).encode("utf-8-sig"),
                                f"comparison_{date_from}_{date_to}.csv", "text/csv")
            problems = result_df[result_df["status"] != "✓ متطابق"]
            ec2.download_button("🚨 Export المشاكل فقط",
                                problems.to_csv(index=False).encode("utf-8-sig"),
                                f"problems_{date_from}_{date_to}.csv", "text/csv")

            with st.expander("👁️ بيانات SofaScore الخام"):
                sf_s = st.text_input("بحث في SofaScore", key="sf_s")
                sf_disp = st.session_state.sf_df
                if sf_s:
                    sf_disp = sf_disp[
                        sf_disp["home_team"].str.contains(sf_s, case=False, na=False) |
                        sf_disp["away_team"].str.contains(sf_s, case=False, na=False) |
                        sf_disp["tournament"].str.contains(sf_s, case=False, na=False)
                    ]
                st.dataframe(sf_disp, use_container_width=True, height=300)

    elif comp_file is None or match_file is None:
        st.info("⬆️ ارفع ملف البطولات وملف الماتشات عشان تبدأ")
