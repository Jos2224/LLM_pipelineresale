-- Negociacion de COMPRA: el bot le escribe al vendedor y regatea.
-- (La tabla `mensaje` es la otra punta: gente que te pregunta a TI.)
--
--   docker exec -i cazador-postgres psql -U cazador -d cazador < db/002-negociacion.sql

-- p_max ahora significa TECHO (V_liq / 1.5). Se agrega el objetivo (V_liq / 2)
-- y el multiplo de reventa, que es el numero que de verdad miras en la alerta.
ALTER TABLE oportunidad ADD COLUMN IF NOT EXISTS objetivo numeric(12,2);
ALTER TABLE oportunidad ADD COLUMN IF NOT EXISTS multiplo numeric(8,3);
ALTER TABLE oportunidad DROP CONSTRAINT IF EXISTS oportunidad_estado_check;
ALTER TABLE oportunidad ADD CONSTRAINT oportunidad_estado_check CHECK (estado IN (
  'nueva','avisada','negociando','acordada','comprar','ignorar','watchlist','comprada'));

CREATE TABLE IF NOT EXISTS negociacion (
  id              bigserial PRIMARY KEY,
  oportunidad     bigint UNIQUE REFERENCES oportunidad(id) ON DELETE CASCADE,
  item_externo    text,              -- id de ML de la publicacion del vendedor
  estado          text DEFAULT 'por_saludar' CHECK (estado IN (
                    'por_saludar',   -- aprobaste, todavia no escribe
                    'saludo',        -- ya saludo, espera respuesta
                    'ofertando',     -- ya hay conversacion, esta regateando
                    'acordado',      -- cerro trato dentro del techo
                    'rechazado',     -- el vendedor no baja lo suficiente
                    'sin_respuesta', -- nunca contesto
                    'cancelada')),   -- la paraste tu
  ronda           int DEFAULT 0,     -- cuantos mensajes mando el bot
  precio_pedido   numeric(12,2),     -- lo que pedia al empezar
  precio_objetivo numeric(12,2),     -- donde quiere cerrar  (V_liq / 2)
  precio_techo    numeric(12,2),     -- limite duro, jamas lo pasa (V_liq / 1.5)
  precio_acordado numeric(12,2),
  pregunta_abierta text,             -- id de la pregunta de ML esperando respuesta
  ultimo_mov      timestamptz DEFAULT now(),
  creada          timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_negoc_estado ON negociacion (estado, ultimo_mov);

CREATE TABLE IF NOT EXISTS negociacion_msg (
  id           bigserial PRIMARY KEY,
  negociacion  bigint REFERENCES negociacion(id) ON DELETE CASCADE,
  direccion    text CHECK (direccion IN ('sale','entra')),
  texto        text,
  id_externo   text,
  ts           timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_negoc_msg ON negociacion_msg (negociacion, ts);

-- 'preparando' = el publicador armo el borrador y espera tu boton. Recien al
-- apretar pasa a 'borrador', que es lo que los scripts 22 y 23 recogen.
ALTER TABLE publicacion DROP CONSTRAINT IF EXISTS publicacion_estado_check;
ALTER TABLE publicacion ADD CONSTRAINT publicacion_estado_check CHECK (estado IN (
  'preparando','borrador','activa','pausada','vendida','cerrada','rechazada'));

-- Un item no puede tener dos publicaciones en el mismo marketplace. Sin esto,
-- apretar el boton dos veces publicaba dos veces lo mismo.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_pub_inv_mkt ON publicacion (inventario, marketplace);

-- Cuantas negociaciones se abrieron hoy (freno anti-spam de ML).
CREATE OR REPLACE VIEW negociaciones_hoy AS
  SELECT count(*) AS n FROM negociacion WHERE creada::date = current_date;
