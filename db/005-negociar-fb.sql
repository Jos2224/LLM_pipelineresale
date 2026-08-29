-- Negociar la COMPRA por Messenger, no solo por preguntas de MercadoLibre.
--
--   docker exec -i cazador-postgres psql -U cazador -d cazador < db/005-negociar-fb.sql

-- De que lado se negocia. 'ml' usa preguntas publicas por la API; 'fb' usa
-- Messenger por el navegador. La escalera de precios es la MISMA en los dos:
-- la decide app/pricing.siguiente_oferta y nunca pasa del techo.
ALTER TABLE negociacion ADD COLUMN IF NOT EXISTS canal text DEFAULT 'ml'
  CHECK (canal IN ('ml', 'fb'));

-- En ML lo que se espera es la respuesta a una pregunta concreta
-- (pregunta_abierta). En Messenger es un hilo, y lo que hay que saber es que
-- burbujas ya vimos para no volver a leerlas como nuevas.
ALTER TABLE negociacion ADD COLUMN IF NOT EXISTS hilo text;
ALTER TABLE negociacion ADD COLUMN IF NOT EXISTS url_item text;

CREATE INDEX IF NOT EXISTS idx_negoc_canal ON negociacion (canal, estado, ultimo_mov);

-- Cuenta las negociaciones nuevas de hoy POR CANAL: el tope de ML y el de FB
-- son distintos y no deben comerse entre ellos.
CREATE OR REPLACE VIEW negociaciones_hoy_canal AS
  SELECT canal, count(*) AS n
    FROM negociacion
   WHERE creada::date = current_date
   GROUP BY canal;
