# ⚽ Match Data Comparator

أداة Streamlit لمقارنة بيانات الماتشات من قاعدة البيانات مع SofaScore.

## الميزات
- جلب ماتشات SofaScore تلقائياً بالتاريخ
- مقارنة fuzzy على أسماء الفرق
- فلتر بالبطولة / التاريخ / الحالة
- جدول mapping لأسماء الفرق المختلفة
- Export نتائج كـ CSV

## طريقة الرفع على Streamlit Cloud

### 1. ارفع الكود على GitHub
```bash
git init
git add .
git commit -m "first commit"
git remote add origin https://github.com/YOUR_USERNAME/match-comparator.git
git push -u origin main
```

### 2. اربط مع Streamlit Cloud
1. روح على https://share.streamlit.io
2. اضغط "New app"
3. اختار الـ repository
4. الـ Main file path: `app.py`
5. اضغط Deploy

## ملفات المشروع
```
match_comparator/
├── app.py              # الـ app الرئيسي
├── requirements.txt    # المكتبات المطلوبة
└── README.md
```

## الـ CSV المطلوب من DBeaver

### ملف الماتشات (matches.csv)
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

### ملف البطولات (competitions.csv)
```sql
SELECT DISTINCT
    c3.id AS competition_id,
    c3.name AS competition,
    c4.name AS competition_country,
    s.name AS season
FROM competition_season cs
LEFT JOIN competitions c3 ON cs.competition_id = c3.id
LEFT JOIN countries c4 ON c3.country_id = c4.id
LEFT JOIN season s ON cs.season_id = s.id
WHERE s.id IN (351, 316, 355, 356)
ORDER BY c4.name, c3.name
```

## ملاحظات مهمة
- SofaScore API غير رسمي — ممكن يتغير في أي وقت
- الـ cache بيخزن البيانات 5 دقايق عشان ما تتحملش كتير
- لو الـ API اتبلوك، هنضيف Apify كـ backup
