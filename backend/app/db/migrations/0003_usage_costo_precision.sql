-- 0003 — Costo con 10 decimales: un embedding cuesta fracciones de millonésima de dólar y
-- numeric(12, 6) lo redondeaba.

alter table usage alter column cost_usd type numeric(18, 10);
