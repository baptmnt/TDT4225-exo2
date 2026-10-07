import ast
from pathlib import Path

import pandas as pd
from haversine import Unit, haversine
from tabulate import tabulate

from DbConnector import DbConnector


CHUNK_SIZE = 10_000
INSERT_BATCH_SIZE = 5_000
CSV_FILE_PATH = Path(__file__).resolve().parent.parent / "corrected_final" / "porto.csv"
SCHEMA_FILE_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


class InsertData:

    def __init__(self):
        self.connection = DbConnector(DATABASE="exo2_bis")
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor

    def run_script(self, script_path):
        script = Path(script_path).read_text()
        for _ in self.cursor.execute(script, multi=True):
            pass
        self.db_connection.commit()

    def create_tables(self):
        self.run_script(SCHEMA_FILE_PATH)

    def show_tables(self):
        self.cursor.execute("SHOW TABLES")
        rows = self.cursor.fetchall()
        print(tabulate(rows, headers=self.cursor.column_names))

    @staticmethod
    def distance_between(first_point, second_point):
        """Return the distance in meters between two [longitude, latitude] points."""
        first_coordinate = (first_point[1], first_point[0])
        second_coordinate = (second_point[1], second_point[0])
        return haversine(first_coordinate, second_coordinate, unit=Unit.METERS)

    @classmethod
    def prepare_row(cls, row):
        polyline = ast.literal_eval(row.POLYLINE)
        start_time = pd.to_datetime(row.TIMESTAMP, unit="s").to_pydatetime()
        num_points = len(polyline)
        distance_m = sum(
            cls.distance_between(first_point, second_point)
            for first_point, second_point in zip(polyline, polyline[1:])
        )

        trip = (
            row.TRIP_ID,
            row.CALL_TYPE,
            None if pd.isna(row.ORIGIN_CALL) else row.ORIGIN_CALL,
            None if pd.isna(row.ORIGIN_STAND) else row.ORIGIN_STAND,
            row.TAXI_ID,
            start_time,
            num_points,
            distance_m,
        )
        points = [
            (
                row.TRIP_ID,
                sequence_number,
                point[0],
                point[1],
                start_time + pd.Timedelta(seconds=15 * sequence_number),
            )
            for sequence_number, point in enumerate(polyline)
        ]
        return trip, points

    def insert_chunk(self, chunk):
        taxi_ids = [(taxi_id,) for taxi_id in chunk.TAXI_ID.drop_duplicates()]
        trips = []
        trajectory_points = []

        for row in chunk.itertuples(index=False):
            trip, points = self.prepare_row(row)
            trips.append(trip)
            trajectory_points.extend(points)

        self.cursor.executemany(
            "INSERT IGNORE INTO Taxi (taxi_id) VALUES (%s)",
            taxi_ids,
        )
        self.cursor.executemany(
            """INSERT INTO Trip
                (trip_id, call_type, origin_call, origin_stand, taxi_id,
                 start_time, num_points, distance_m)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            trips,
        )
        for batch_start in range(0, len(trajectory_points), INSERT_BATCH_SIZE):
            point_batch = trajectory_points[batch_start:batch_start + INSERT_BATCH_SIZE]
            self.cursor.executemany(
                """INSERT INTO TrajectoryPoint
                    (trip_id, seq_num, longitude, latitude, point_time)
                    VALUES (%s, %s, %s, %s, %s)""",
                point_batch,
            )

        self.db_connection.commit()
        return len(trips), len(trajectory_points)

    def insert_csv_data(self, file_path=CSV_FILE_PATH):
        columns = [
            "TRIP_ID",
            "CALL_TYPE",
            "ORIGIN_CALL",
            "ORIGIN_STAND",
            "TAXI_ID",
            "TIMESTAMP",
            "POLYLINE",
        ]
        total_trips = 0
        total_points = 0

        for chunk in pd.read_csv(file_path, usecols=columns, chunksize=CHUNK_SIZE):
            trips, points = self.insert_chunk(chunk)
            total_trips += trips
            total_points += points
            print(
                f"Inserted {trips:,} trips and {points:,} trajectory points "
                f"({total_trips:,} trips total)."
            )

        print(f"Inserted {total_trips:,} trips and {total_points:,} trajectory points in total.")


def main():
    program = None
    try:
        program = InsertData()
        program.create_tables()
        program.show_tables()
        program.insert_csv_data()
        print("Data insertion completed successfully.")
    except Exception as error:
        if program:
            program.db_connection.rollback()
        print("ERROR: Failed to insert data:", error)
    finally:
        if program:
            program.connection.close_connection()


if __name__ == "__main__":
    main()