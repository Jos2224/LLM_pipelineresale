-- Precios de MercadoLibre Y de Facebook, en paralelo.
--
--   docker exec -i cazador-postgres psql -U cazador -d cazador < db/006-dos-mercados.sql
--
-- Antes solo ML alimentaba el indice de precios: los items de Facebook eran
-- "una compra, no una referencia de venta". Es cierto que no son lo mismo —
-- en FB la gente pide menos que en ML — pero tratarlos como si no existieran
-- deja al sistema sin poder valuar NADA mientras no haya login de ML.
--
-- Ahora hay un indice por mercado. Se comparan los dos y se sabe cual se uso.
-- Como el usuario COMPRA en FB y VENDE en ML, la referencia buena para saber
-- "en cuanto lo revendo" es la de ML; la de FB es el respaldo, y por ser mas
-- baja es conservadora: hace perder oportunidades, no inventarlas.

ALTER TABLE precio_obs ADD COLUMN IF NOT EXISTS mercado text DEFAULT 'ml'
  CHECK (mercado IN ('ml', 'fb'));
CREATE INDEX IF NOT EXISTS idx_precio_obs_mercado
  ON precio_obs (producto, mercado, tramo, fecha DESC);

-- Lo ya guardado vino todo de ML.
UPDATE precio_obs SET mercado = 'ml' WHERE mercado IS NULL;

ALTER TABLE indice_precio ADD COLUMN IF NOT EXISTS mercado text NOT NULL DEFAULT 'ml'
  CHECK (mercado IN ('ml', 'fb'));
ALTER TABLE indice_precio DROP CONSTRAINT IF EXISTS indice_precio_pkey;
ALTER TABLE indice_precio ADD PRIMARY KEY (producto, tramo, mercado);
