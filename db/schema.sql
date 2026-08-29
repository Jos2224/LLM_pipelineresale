-- Cazador — esquema. Se carga solo la primera vez que arranca el postgres.
-- Para aplicarlo a mano:  docker exec -i cazador-postgres psql -U cazador -d cazador < db/schema.sql

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- secretos
-- Tokens OAuth de MercadoLibre. Una sola fila (id=1). El refresh token se
-- rota solo cada vez que se renueva el access token.
CREATE TABLE IF NOT EXISTS oauth_ml (
  id             int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  ml_user_id     bigint,
  nickname       text,
  access_token   text,
  refresh_token  text,
  expira_en      timestamptz,
  actualizado    timestamptz DEFAULT now()
);

-- Clave/valor generico: cursores, ultimo update_id de Telegram, flags.
CREATE TABLE IF NOT EXISTS kv (
  clave  text PRIMARY KEY,
  valor  jsonb NOT NULL,
  ts     timestamptz DEFAULT now()
);

-- ---------------------------------------------------------------- fuentes
CREATE TABLE IF NOT EXISTS fuente (
  id      serial PRIMARY KEY,
  nombre  text UNIQUE NOT NULL,
  tipo    text NOT NULL CHECK (tipo IN ('ml','fb','aduanas')),
  activa  boolean DEFAULT true
);

INSERT INTO fuente (nombre, tipo) VALUES
  ('mercadolibre', 'ml'), ('aduanas', 'aduanas'), ('facebook', 'fb')
ON CONFLICT (nombre) DO NOTHING;

-- Lo que se ve afuera, crudo, sin interpretar. hash evita duplicados.
CREATE TABLE IF NOT EXISTS item_raw (
  id           bigserial PRIMARY KEY,
  fuente       int REFERENCES fuente(id),
  url          text,
  id_externo   text,
  titulo       text NOT NULL,
  precio       numeric(12,2),
  moneda       text DEFAULT 'CLP',
  fotos        text[] DEFAULT '{}',
  crudo        jsonb DEFAULT '{}',
  visto_en     timestamptz DEFAULT now(),
  hash         text UNIQUE NOT NULL,
  normalizado  boolean DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_item_raw_pend ON item_raw (normalizado, visto_en);
CREATE INDEX IF NOT EXISTS idx_item_raw_titulo ON item_raw USING gin (titulo gin_trgm_ops);

-- ------------------------------------------------------------ catalogo
CREATE TABLE IF NOT EXISTS producto_canon (
  id         bigserial PRIMARY KEY,
  marca      text NOT NULL,
  modelo     text NOT NULL,
  categoria  text,
  specs      jsonb DEFAULT '{}',
  ml_categoria text,
  creado     timestamptz DEFAULT now(),
  UNIQUE (marca, modelo)
);

CREATE TABLE IF NOT EXISTS precio_obs (
  id         bigserial PRIMARY KEY,
  producto   bigint REFERENCES producto_canon(id) ON DELETE CASCADE,
  precio     numeric(12,2) NOT NULL,
  estado     text CHECK (estado IN ('nuevo','usado','reacondicionado','desconocido')),
  vendidos   int DEFAULT 0,
  fecha      timestamptz DEFAULT now(),
  origen     text
);
CREATE INDEX IF NOT EXISTS idx_precio_obs ON precio_obs (producto, fecha DESC);

-- Indice de precios congelado por producto (lo que arma price_index.py).
CREATE TABLE IF NOT EXISTS indice_precio (
  producto   bigint PRIMARY KEY REFERENCES producto_canon(id) ON DELETE CASCADE,
  p25        numeric(12,2),
  p50        numeric(12,2),
  p80        numeric(12,2),
  n_muestras int,
  calculado  timestamptz DEFAULT now()
);

-- ------------------------------------------------------------ oportunidad
CREATE TABLE IF NOT EXISTS oportunidad (
  id        bigserial PRIMARY KEY,
  item_raw  bigint UNIQUE REFERENCES item_raw(id) ON DELETE CASCADE,
  producto  bigint REFERENCES producto_canon(id),
  v_liq     numeric(12,2),
  p_max     numeric(12,2),
  score     numeric(8,4),
  g_conocido boolean DEFAULT true,   -- false = remate sin G, la alerta lo marca
  estado    text DEFAULT 'nueva' CHECK (estado IN ('nueva','avisada','comprar','ignorar','watchlist','comprada')),
  creada    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_oport_estado ON oportunidad (estado, score DESC);

-- ------------------------------------------------------------ inventario
CREATE TABLE IF NOT EXISTS inventario (
  id           bigserial PRIMARY KEY,
  codigo       text UNIQUE NOT NULL,
  producto     bigint REFERENCES producto_canon(id),
  titulo       text,
  condicion    text DEFAULT 'usado',
  costo        numeric(12,2),
  piso_precio  numeric(12,2),
  piso_manual  boolean DEFAULT false,
  fotos        text[] DEFAULT '{}',
  estado       text DEFAULT 'sin_fotos'
                 CHECK (estado IN ('sin_fotos','listo','borrador','publicado','vendido','pausado')),
  origen       text,
  creado       timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS publicacion (
  id           bigserial PRIMARY KEY,
  inventario   bigint REFERENCES inventario(id) ON DELETE CASCADE,
  marketplace  text NOT NULL CHECK (marketplace IN ('ml','fb')),
  id_externo   text,
  url          text,
  titulo       text,
  descripcion  text,
  precio       numeric(12,2),
  estado       text DEFAULT 'borrador'
                 CHECK (estado IN ('borrador','activa','pausada','vendida','cerrada','rechazada')),
  visitas      int DEFAULT 0,
  ultimo_ajuste timestamptz,
  fecha        timestamptz DEFAULT now(),
  UNIQUE (marketplace, id_externo)
);
CREATE INDEX IF NOT EXISTS idx_pub_estado ON publicacion (estado, fecha);

CREATE TABLE IF NOT EXISTS mensaje (
  id            bigserial PRIMARY KEY,
  publicacion   bigint REFERENCES publicacion(id) ON DELETE CASCADE,
  id_externo    text,
  direccion     text CHECK (direccion IN ('entra','sale')),
  tipo          text DEFAULT 'pregunta',   -- pregunta | mensaje | oferta
  texto         text,
  monto_oferta  numeric(12,2),
  respondido_por text CHECK (respondido_por IN ('bot','jose')),
  estado        text DEFAULT 'nuevo' CHECK (estado IN ('nuevo','respondido','escalado','ignorado')),
  ts            timestamptz DEFAULT now(),
  UNIQUE (id_externo, direccion)
);

-- ------------------------------------------------------------ mercado
CREATE TABLE IF NOT EXISTS tendencia (
  id         bigserial PRIMARY KEY,
  categoria  text,
  fecha      date DEFAULT current_date,
  p50        numeric(12,2),
  dias_venta numeric(6,2),
  n          int,
  UNIQUE (categoria, fecha)
);

-- Remates cerrados: lo que backtest.py usa para calibrar B = P0 * sqrt(G).
CREATE TABLE IF NOT EXISTS remate_cierre (
  id          bigserial PRIMARY KEY,
  item_raw    bigint REFERENCES item_raw(id),
  p0          numeric(12,2),
  g           int,
  b_estimado  numeric(12,2),
  precio_real numeric(12,2),
  fecha       timestamptz DEFAULT now()
);

-- ------------------------------------------------------------ auditoria
CREATE TABLE IF NOT EXISTS job_log (
  id      bigserial PRIMARY KEY,
  job     text NOT NULL,
  ok      boolean,
  detalle text,
  ms      int,
  ts      timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_job_log ON job_log (job, ts DESC);
