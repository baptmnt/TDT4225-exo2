CREATE TABLE IF NOT EXISTS Taxi (
    taxi_id BIGINT PRIMARY KEY
);


CREATE TABLE IF NOT EXISTS Trip (
    trip_id BIGINT PRIMARY KEY,
    call_type CHAR(1) NOT NULL,           -- 'A','B','C'
    origin_call INT NULL,
    origin_stand INT NULL,
    taxi_id BIGINT NOT NULL,
    start_time DATETIME NOT NULL,         -- converti depuis TIMESTAMP (UTC)
    num_points INT NOT NULL,              -- dérivé, pratique pour filtrer
    distance_m DOUBLE NOT NULL,                 -- dérivé, pratique pour filtrer
    FOREIGN KEY (taxi_id) REFERENCES Taxi(taxi_id),

    INDEX idx_taxi (taxi_id),
    INDEX idx_start_time (start_time),
    INDEX idx_call_type (call_type)
);

CREATE TABLE IF NOT EXISTS TrajectoryPoint (
    trip_id BIGINT NOT NULL,
    seq_num INT NOT NULL,                 -- ordre du point dans la polyline (0,1,2,...)
    longitude DOUBLE NOT NULL,
    latitude DOUBLE NOT NULL,
    point_time DATETIME NOT NULL,         -- start_time + seq_num * 15s
    PRIMARY KEY (trip_id, seq_num),
    FOREIGN KEY (trip_id) REFERENCES Trip(trip_id)
);