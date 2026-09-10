-- P4/F9: профиль провайдера модели у дела (NULL — по умолчанию/по конфиденциальности)
ALTER TABLE workspace ADD COLUMN IF NOT EXISTS provider TEXT;
