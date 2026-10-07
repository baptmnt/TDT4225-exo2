from DbConnector import DbConnector
from tabulate import tabulate
from datetime import datetime


class ExerciceProgram:

    def __init__(self):
        self.connection = DbConnector()
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor


    def show_tables(self):
        self.cursor.execute("SHOW TABLES")
        rows = self.cursor.fetchall()
        print(tabulate(rows, headers=self.cursor.column_names))

    def question_1(self):
        self.cursor.execute("SELECT COUNT(*) FROM Taxi")
        result = self.cursor.fetchone()
        taxi_count = result[0]

        self.cursor.execute("SELECT COUNT(*) FROM Trip")
        result = self.cursor.fetchone()
        trip_count = result[0]

        self.cursor.execute("SELECT COUNT(*) FROM TrajectoryPoint")
        result = self.cursor.fetchone()
        trajectory_count = result[0]

        print("SELECT COUNT(*) FROM Taxi")
        print(f"Number of taxis: {taxi_count:,}\n")
        
        print("SELECT COUNT(*) FROM Trip")
        print(f"Number of trips: {trip_count:,}\n")

        print("SELECT COUNT(*) FROM TrajectoryPoint")
        print(f"Number of trajectory points: {trajectory_count:,}\n")

    def question_2(self):
        self.cursor.execute("SELECT COUNT(*)/COUNT(DISTINCT taxi_id) FROM Trip")
        result = self.cursor.fetchone()
        print("SELECT COUNT(*)/COUNT(DISTINCT taxi_id) FROM Trip")
        print(f"Average number of trips per taxi: {round(result[0],2)}\n")

    def question_3(self):
        self.cursor.execute("SELECT taxi_id, COUNT(*) FROM Trip GROUP BY taxi_id ORDER BY COUNT(*) DESC LIMIT 20")
        result = self.cursor.fetchall()
        print("SELECT taxi_id, COUNT(*) FROM Trip GROUP BY taxi_id ORDER BY COUNT(*) DESC LIMIT 20")

        groups = [result[index:index + 7] for index in range(0, len(result), 7)]
        rows = []
        for row_index in range(max((len(group) for group in groups), default=0)):
            row = []
            for group_index, group in enumerate(groups):
                if row_index < len(group):
                    taxi_id, trip_count = group[row_index]
                    row.extend((group_index * 7 + row_index + 1, taxi_id, trip_count))
                else:
                    row.extend(("", "", ""))
                if group_index < len(groups) - 1:
                    row.append("")
            rows.append(row)

        headers = []
        for group_index in range(len(groups)):
            headers.extend(("#top", "taxi_id", "number"))
            if group_index < len(groups) - 1:
                headers.append("")
        print(tabulate(
            rows,
            headers=headers,
            tablefmt="plain",
        ))

    def question_4a(self):
        query = """
            WITH call_counts AS (
                SELECT taxi_id, call_type, COUNT(*) AS trip_count
                FROM Trip
                GROUP BY taxi_id, call_type
            ), ranked_calls AS (
                SELECT
                    taxi_id,
                    call_type,
                    trip_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY taxi_id
                        ORDER BY trip_count DESC, call_type ASC
                    ) AS ranking
                FROM call_counts
            )
            SELECT taxi_id, call_type, trip_count
            FROM ranked_calls
            WHERE ranking = 1
            ORDER BY taxi_id ASC
            LIMIT 10
        """
        self.cursor.execute(query)
        result = self.cursor.fetchall()

        print(query)
        print(tabulate(
            result,
            headers=["taxi_id", "call_type", "number"],
            tablefmt="grid",
        ))

    def question_4b(self):
        self.cursor.execute("""
            SELECT
                call_type,
                SUM(CASE WHEN HOUR(start_time) BETWEEN 0 AND 5  THEN 1 ELSE 0 END) / COUNT(*) *100 AS share_00_06,
                SUM(CASE WHEN HOUR(start_time) BETWEEN 6 AND 11 THEN 1 ELSE 0 END) / COUNT(*) *100 AS share_06_12,
                SUM(CASE WHEN HOUR(start_time) BETWEEN 12 AND 17 THEN 1 ELSE 0 END) / COUNT(*) *100 AS share_12_18,
                SUM(CASE WHEN HOUR(start_time) BETWEEN 18 AND 23 THEN 1 ELSE 0 END) / COUNT(*) *100 AS share_18_24
            FROM Trip
            GROUP BY call_type;
        """)

        result = self.cursor.fetchall()
        print("""
SELECT
    call_type,
    SUM(CASE WHEN HOUR(start_time) BETWEEN 0 AND 5  THEN 1 ELSE 0 END) / COUNT(*) *100 AS share_00_06,
    SUM(CASE WHEN HOUR(start_time) BETWEEN 6 AND 11 THEN 1 ELSE 0 END) / COUNT(*) *100 AS share_06_12,
    SUM(CASE WHEN HOUR(start_time) BETWEEN 12 AND 17 THEN 1 ELSE 0 END) / COUNT(*) *100 AS share_12_18,
    SUM(CASE WHEN HOUR(start_time) BETWEEN 18 AND 23 THEN 1 ELSE 0 END) / COUNT(*) *100 AS share_18_24
FROM Trip
GROUP BY call_type;
        """)
        print(tabulate(
            result,
            headers=["call_type", "share_00_06", "share_06_12", "share_12_18", "share_18_24"],
            tablefmt="grid",
        ))

        self.cursor.execute("""
            SELECT
                call_type,
                AVG((num_points - 1) * 15)/60 AS avg_duration_min,
                AVG(distance_m) AS avg_distance_m

            FROM Trip
            GROUP BY call_type;
        """)

        result = self.cursor.fetchall()
        print("""
SELECT
    call_type,
    AVG((num_points - 1) * 15)/60 AS avg_duration_min,
    AVG(distance_m) AS avg_distance_m
FROM Trip
GROUP BY call_type;
        """)
        print(tabulate(
            result,
            headers=["call_type", "avg_duration_min", "avg_distance_m"],
            tablefmt="grid",
        ))

    def question_5(self):
        self.cursor.execute("SELECT taxi_id, SUM(num_points)*15/3600 AS taxi_hours_driven, SUM(distance_m)/1000 AS taxi_km_driven FROM Trip GROUP BY taxi_id ORDER BY taxi_hours_driven DESC LIMIT 10")
        result = self.cursor.fetchall()
        print("""
SELECT 
    taxi_id, 
    SUM(num_points)*15/3600 AS taxi_hours_driven, 
    SUM(distance_m)/1000 AS taxi_km_driven 
FROM Trip 
GROUP BY taxi_id 
ORDER BY taxi_hours_driven DESC 
LIMIT 10""")
        print(tabulate(
            result,
            headers=["taxi_id", "taxi_hours_driven", "taxi_km_driven"],
            tablefmt="grid",
        ))

    def question_6(self):
        longitude, latitude = -8.62911, 41.15794
        self.cursor.execute(f"""
            SELECT COUNT(DISTINCT trip_id)
            FROM TrajectoryPoint
            WHERE ST_DISTANCE_SPHERE(
                POINT({longitude}, {latitude}),
                POINT(longitude, latitude)
                ) <=100
            """)
        result = self.cursor.fetchone()
        print(f"""
SELECT COUNT(DISTINCT trip_id)
FROM TrajectoryPoint
WHERE ST_DISTANCE_SPHERE(
    POINT({longitude}, {latitude}),
    POINT(longitude, latitude)
) <=100""")
        print(f"Number of trips within 100 meters of point ({longitude}, {latitude}): {result[0]:,}\n")

    def question_7(self):
        self.cursor.execute(f"""
            SELECT COUNT(*)
            FROM Trip
            WHERE num_points <=3
            """)
        result = self.cursor.fetchone()
        print(f"""SELECT COUNT(*) FROM Trip WHERE num_points <3""")
        print(f"Number of invalid trips (fewer than 3 GPS points): {result[0]:,}\n")

    def question_8(self):
        self.cursor.execute(f"""
            SELECT COUNT(*)
            FROM Trip
            WHERE DATEDIFF(DATE_ADD(start_time, INTERVAL (num_points-1)*15 SECOND), start_time) > 0
            """)
        result = self.cursor.fetchone()
        print(f"""
SELECT COUNT(*)
FROM Trip
WHERE DATEDIFF(DATE_ADD(start_time, INTERVAL (num_points-1)*15 SECOND), start_time) > 0""")
        print(f"Number of midnight crossers: {result[0]:,}\n")

    def question_9(self):
        self.cursor.execute(f"""
            SELECT 
                COUNT(*), 
                AVG(ST_DISTANCE_SPHERE(
                    POINT(start.longitude, start.latitude),
                    POINT(end.longitude, end.latitude)
                    ))

            FROM Trip t
            JOIN TrajectoryPoint start
                ON t.trip_id = start.trip_id AND start.seq_num = 0
            JOIN TrajectoryPoint end
                ON t.trip_id = end.trip_id AND end.seq_num = (t.num_points -1)

            WHERE ST_DISTANCE_SPHERE(
                POINT(start.longitude, start.latitude),
                POINT(end.longitude, end.latitude)
                ) <= 50
            """)
        result = self.cursor.fetchone()
        print(f"""
SELECT 
    COUNT(*), 
    AVG(ST_DISTANCE_SPHERE(
        POINT(start.longitude, start.latitude),
        POINT(end.longitude, end.latitude)
        ))

FROM Trip t
JOIN TrajectoryPoint start
    ON t.trip_id = start.trip_id AND start.seq_num = 0
JOIN TrajectoryPoint end
    ON t.trip_id = end.trip_id AND end.seq_num = (t.num_points -1)

WHERE ST_DISTANCE_SPHERE(
    POINT(start.longitude, start.latitude),
    POINT(end.longitude, end.latitude)
    ) <= 50
""")
        print(f"Number of circular trips: {result[0]:,} with an average of {round(result[1],2)}m between the start and end points.\n")

    def question_10(self):
        query = """
WITH gaps AS (
    SELECT
        taxi_id,
        TIMESTAMPDIFF(
            SECOND,
            LAG(DATE_ADD(start_time, INTERVAL (num_points - 1) * 15 SECOND))
                OVER (PARTITION BY taxi_id ORDER BY start_time, num_points),
            start_time
        ) AS idle_seconds
    FROM Trip
)
SELECT
    taxi_id,
    ROUND(
        SUM(IF(idle_seconds <= 8*3600, idle_seconds, 0))
        / (1 + SUM(idle_seconds <= 8*3600)) / 60,
        2
    ) AS average_idle_time_minutes,
    1 + SUM(idle_seconds <= 8*3600) AS number_of_consecutive_trips,
    SUM(idle_seconds > 8*3600)      AS number_of_non_consecutive_trips
FROM gaps
GROUP BY taxi_id
ORDER BY average_idle_time_minutes DESC
LIMIT 20;
        """
        self.cursor.execute(query)
        result = self.cursor.fetchall()

        print(query)
        print(tabulate(
            result,
            headers=[
                "taxi_id",
                "average_idle_time_minutes",
                "number_of_consecutive_trips",
                "number_of_non_consecutive_trips",
            ],
            tablefmt="grid",
        ))






def main():
    program = None
    try:
        program = ExerciceProgram()
        time = datetime.now()

        #program.question_1()
        #program.question_2()
        #program.question_3()
        #program.question_4a()
        #program.question_4b()
        #program.question_5()
        #program.question_6()
        #program.question_7()
        #program.question_8()
        #program.question_9()
        program.question_10()





        print(f"Elapsed time: {(datetime.now()-time).total_seconds()}s")


    except Exception as e:
        raise e
        print("ERROR: Failed to use database:", e)
    finally:
        if program:
            program.connection.close_connection()


if __name__ == '__main__':
    main()
