-- Facebook Marketplace: contestar en los hilos, y que el boton [Enviar] de
-- Telegram de verdad mande algo (antes empujaba a una cola que nadie leia).
--
--   docker exec -i cazador-postgres psql -U cazador -d cazador < db/003-fb.sql

-- El hilo de Marketplace al que pertenece el mensaje. En ML no se usa: ahi el
-- id_externo de la pregunta ya alcanza para responderla.
ALTER TABLE mensaje ADD COLUMN IF NOT EXISTS hilo text;

-- Une la respuesta que redacto el bot con la pregunta que la origino. Sin
-- esto, apretar [Enviar] no tenia forma de saber QUE texto mandar ni A QUIEN.
ALTER TABLE mensaje ADD COLUMN IF NOT EXISTS responde_a bigint
  REFERENCES mensaje(id) ON DELETE SET NULL;

-- ml | fb. Se podria deducir por el join a publicacion, pero la cola de
-- envios necesita filtrar sin joins y mezclar canales seria mandar el texto
-- por el lado equivocado.
ALTER TABLE mensaje ADD COLUMN IF NOT EXISTS canal text DEFAULT 'ml'
  CHECK (canal IN ('ml', 'fb'));

-- Los borradores esperando tu boton son estado='nuevo' y direccion='sale'.
CREATE INDEX IF NOT EXISTS idx_msg_pend ON mensaje (direccion, estado, canal);
CREATE INDEX IF NOT EXISTS idx_msg_hilo ON mensaje (hilo);
