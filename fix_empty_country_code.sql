-- ═══════════════════════════════════════════════════════════════
-- FIX: Заполнить пустой country_code у билетов по city_name → clubs → countries
-- 
-- Проблема: билеты из Rotterdam (57 шт), Warsaw (71 шт) и др. имеют
-- пустой country_code, из-за чего попадают в группу "Неизвестно"
-- и невидимы для country_manager (напр. Катя/NL).
--
-- Логика: city_name билета → clubs.city_english → countries.country_code
-- ═══════════════════════════════════════════════════════════════

-- 1. Посмотреть текущее состояние (билеты с пустым country_code):
SELECT t.id, t.order_id, t.city_name, t.country_code, t.club_id, t.event_name
FROM tickets t
WHERE t.country_code IS NULL OR t.country_code = ''
ORDER BY t.city_name, t.id;

-- 2. Превью: что будет обновлено
SELECT t.id, t.city_name, t.country_code AS old_country,
       co.country_code AS new_country, c.club_id AS new_club_id
FROM tickets t
JOIN clubs c ON LOWER(c.city_english) = LOWER(t.city_name)
JOIN countries co ON c.country_id = co.country_id
WHERE (t.country_code IS NULL OR t.country_code = '')
ORDER BY co.country_code, t.city_name;

-- 3. ОБНОВИТЬ country_code (и club_id если пустой):
UPDATE tickets t
SET country_code = co.country_code,
    club_id = COALESCE(t.club_id, c.club_id)
FROM clubs c
JOIN countries co ON c.country_id = co.country_id
WHERE LOWER(c.city_english) = LOWER(t.city_name)
  AND (t.country_code IS NULL OR t.country_code = '');

-- 4. Проверить результат:
SELECT country_code, city_name, COUNT(*) as cnt
FROM tickets
WHERE country_code IS NOT NULL AND country_code != ''
GROUP BY country_code, city_name
ORDER BY country_code, city_name;

-- 5. Проверить, остались ли ещё билеты без country_code:
SELECT COUNT(*) as still_empty
FROM tickets
WHERE country_code IS NULL OR country_code = '';
