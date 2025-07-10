-- Qurilmalar holati tarixi uchun jadval
CREATE TABLE IF NOT EXISTS device_history (
    id SERIAL PRIMARY KEY,
    device_id INTEGER REFERENCES devices(id),
    status BOOLEAN NOT NULL, -- true = online, false = offline
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    CONSTRAINT valid_timerange CHECK (end_time IS NULL OR end_time > start_time)
);

-- Indekslar
CREATE INDEX IF NOT EXISTS idx_device_history_device_id ON device_history(device_id);
CREATE INDEX IF NOT EXISTS idx_device_history_timerange ON device_history(start_time, end_time);

-- Statistika uchun view
CREATE OR REPLACE VIEW device_status_stats AS
WITH closed_periods AS (
    SELECT 
        device_id,
        status,
        start_time,
        COALESCE(end_time, CURRENT_TIMESTAMP) as end_time,
        EXTRACT(EPOCH FROM (COALESCE(end_time, CURRENT_TIMESTAMP) - start_time))/3600 as hours
    FROM device_history
)
SELECT 
    device_id,
    SUM(CASE WHEN status = true THEN hours ELSE 0 END) as online_hours,
    SUM(CASE WHEN status = false THEN hours ELSE 0 END) as offline_hours,
    SUM(hours) as total_hours
FROM closed_periods
GROUP BY device_id; 