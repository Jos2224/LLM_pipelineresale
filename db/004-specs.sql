-- El indice de precios ignoraba las specs. Un T480 de 8GB/256 y uno de
-- 32GB/1TB compartian el mismo P50, asi que el multiplo salia mal en las dos
-- direcciones: alertaba compras de 1,6x creyendolas 2,2x, y se perdia gangas
-- reales de 2,2x creyendolas 1,7x.
--
--   docker exec -i cazador-postgres psql -U cazador -d cazador < db/004-specs.sql

-- Cada precio observado guarda con que specs se observo.
ALTER TABLE precio_obs ADD COLUMN IF NOT EXISTS ram_gb   int;
ALTER TABLE precio_obs ADD COLUMN IF NOT EXISTS disco_gb int;
ALTER TABLE precio_obs ADD COLUMN IF NOT EXISTS tramo    text;
CREATE INDEX IF NOT EXISTS idx_precio_obs_tramo ON precio_obs (producto, tramo, fecha DESC);

-- indice_precio pasa de UNA fila por producto a una por (producto, tramo).
-- tramo '*' es el modelo entero: es el que existia antes y sigue siendo el
-- respaldo cuando un tramo puntual no junta muestras suficientes.
ALTER TABLE indice_precio ADD COLUMN IF NOT EXISTS tramo text NOT NULL DEFAULT '*';
ALTER TABLE indice_precio DROP CONSTRAINT IF EXISTS indice_precio_pkey;
ALTER TABLE indice_precio ADD PRIMARY KEY (producto, tramo);

-- Cuanto sube el precio por punto de "puntaje de specs", MEDIDO en las
-- observaciones de ese mismo producto, y cual es el puntaje del equipo tipico.
-- Solo se llenan en la fila '*'. Si no hay datos para medirlos quedan NULL y
-- se usan los de config/policy.yml.
ALTER TABLE indice_precio ADD COLUMN IF NOT EXISTS coef_spec numeric(8,4);
ALTER TABLE indice_precio ADD COLUMN IF NOT EXISTS spec_ref  numeric(8,4);

-- Primer intento: un coeficiente para RAM y otro para disco. Estaba mal — RAM
-- y disco van juntos, asi que cada uno se llevaba el credito del otro y el
-- ajuste se iba al triple. Reemplazados por el puntaje unico de arriba.
ALTER TABLE indice_precio DROP COLUMN IF EXISTS coef_ram;
ALTER TABLE indice_precio DROP COLUMN IF EXISTS coef_disco;
ALTER TABLE indice_precio DROP COLUMN IF EXISTS ram_ref;
ALTER TABLE indice_precio DROP COLUMN IF EXISTS disco_ref;
