"""Simple analyses of the Porto Taxi dataset.

The script produces the project's main checks and plots:
distributions by taxi and date, polyline quality, large GPS jump detection,
ghost trips, and anchor-based correction.

Examples:
    python taxi_distributions_clean.py train.csv --analysis all
    python taxi_distributions_clean.py train.csv --analysis quality,suspicious
"""

import argparse
import ast
import os

import matplotlib.pyplot as plt
import pandas as pd
from haversine import Unit, haversine
from tabulate import tabulate


DEFAULT_CHUNK_SIZE = 10_000
DEFAULT_SAMPLE_SIZE = 200_000
POINT_INTERVAL_SECONDS = 15


def read_metadata(csv_path, chunk_size):
    columns = [
        "TRIP_ID", "TAXI_ID", "CALL_TYPE", "ORIGIN_CALL",
        "ORIGIN_STAND", "TIMESTAMP",
    ]
    chunks = pd.read_csv(csv_path, usecols=columns, chunksize=chunk_size)
    data = pd.concat(chunks, ignore_index=True)
    data["DATETIME"] = pd.to_datetime(data["TIMESTAMP"], unit="s")
    return data


def read_polyline_sample(csv_path, sample_size, include_missing=False):
    data = pd.read_csv(csv_path, nrows=None if sample_size <= 0 else sample_size)
    if not include_missing and "MISSING_DATA" in data:
        is_missing = data["MISSING_DATA"].astype(str).str.strip().str.lower().eq("true")
        data = data[~is_missing].copy()

    data["POINTS"] = data["POLYLINE"].map(parse_polyline)
    data["N_POINTS"] = data["POINTS"].map(len)
    data["DURATION_S"] = (data["N_POINTS"] - 1).clip(lower=0) * POINT_INTERVAL_SECONDS
    return data


def parse_polyline(value):
    if not isinstance(value, str):
        return []
    try:
        points = ast.literal_eval(value)
    except (SyntaxError, ValueError, TypeError):
        return []
    return points if isinstance(points, list) else []


def segment_distances(points):
    return [
        haversine((first[1], first[0]), (second[1], second[0]), unit=Unit.METERS)
        for first, second in zip(points, points[1:])
    ]


def trip_metrics(points):
    distances = segment_distances(points)
    duration = max(len(points) - 1, 0) * POINT_INTERVAL_SECONDS
    total_distance = sum(distances)
    speeds = [distance / POINT_INTERVAL_SECONDS * 3.6 for distance in distances]
    return {
        "DISTANCE_M": total_distance,
        "DURATION_S": duration,
        "AVG_SPEED_KMH": total_distance / duration * 3.6 if duration else 0.0,
        "MAX_SPEED_KMH": max(speeds, default=0.0),
        "SEGMENT_DISTANCES_M": distances,
        "SEGMENT_SPEEDS_KMH": speeds,
    }


def add_metrics(data):
    metrics = data["POINTS"].map(trip_metrics).apply(pd.Series)
    return pd.concat([data, metrics], axis=1)


def save_figure(figure, output_dir, filename):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    print(f"[saved] {path}")


def plot_metadata(data, output_dir):
    counts = data.groupby(["TAXI_ID", "CALL_TYPE"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=["A", "B", "C"], fill_value=0)
    top = counts.sum(axis=1).nlargest(30).sort_values()

    figure, axis = plt.subplots(figsize=(11, 6))
    counts.loc[top.index].plot.barh(ax=axis, stacked=True)
    axis.set_title("Top 30 taxis by number of trips")
    axis.set_xlabel("Number of trips")
    axis.set_ylabel("TAXI_ID")
    save_figure(figure, output_dir, "01_trips_by_taxi.png")

    figure, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    for axis, call_type, color in zip(
        axes, ["A", "B", "C"], ["steelblue", "darkorange", "seagreen"]
    ):
        axis.hist(counts[call_type], bins=50, color=color)
        axis.set_title(f"Type {call_type} trips per taxi")
        axis.set_xlabel("Number of trips")
        axis.set_ylabel("Number of taxis")
    save_figure(figure, output_dir, "02_trips_by_taxi_distribution.png")


def plot_time_distributions(data, output_dir):

    hourly = data.groupby([data["DATETIME"].dt.hour, "CALL_TYPE"]).size()
    hourly = hourly.unstack(fill_value=0).reindex(columns=["A", "B", "C"], fill_value=0)
    figure, axis = plt.subplots(figsize=(10, 5))
    hourly.plot(ax=axis, marker="o")
    axis.set_title("Trips by hour and call type")
    axis.set_xlabel("Hour")
    axis.set_ylabel("Number of trips")
    save_figure(figure, output_dir, "03_trips_by_hour.png")

    weekday = data.groupby([data["DATETIME"].dt.dayofweek, "CALL_TYPE"]).size()
    weekday = weekday.unstack(fill_value=0).reindex(columns=["A", "B", "C"], fill_value=0)
    figure, axis = plt.subplots(figsize=(9, 5))
    weekday.plot.bar(ax=axis)
    axis.set_title("Trips by day of the week")
    axis.set_xlabel("Day (0 = Monday)")
    axis.set_ylabel("Number of trips")
    axis.tick_params(axis="x", rotation=0)
    save_figure(figure, output_dir, "04_trips_by_weekday.png")

    daily = data.set_index("DATETIME").resample("D").size()
    figure, axis = plt.subplots(figsize=(12, 4))
    daily.plot(ax=axis, color="teal")
    axis.set_title("Number of trips per day")
    axis.set_ylabel("Number of trips")
    save_figure(figure, output_dir, "05_trips_by_day.png")


def report_consistency(data, output_dir):
    duplicate_rows = data[data["TRIP_ID"].duplicated(keep=False)]

    has_call = data["ORIGIN_CALL"].notna()
    has_stand = data["ORIGIN_STAND"].notna()
    checks = {
        "A without ORIGIN_CALL": int((data["CALL_TYPE"].eq("A") & ~has_call).sum()),
        "non-A with ORIGIN_CALL": int((data["CALL_TYPE"].ne("A") & has_call).sum()),
        "B without ORIGIN_STAND": int((data["CALL_TYPE"].eq("B") & ~has_stand).sum()),
        "non-B with ORIGIN_STAND": int((data["CALL_TYPE"].ne("B") & has_stand).sum()),
    }
    checks["Duplicate TRIP_IDs"] = duplicate_rows["TRIP_ID"].nunique()
    print("\nConsistency checks")
    print(
        tabulate(
            [[label, count] for label, count in checks.items()],
            headers=["Check", "Number of rows"],
            tablefmt="github",
        )
    )


def plot_quality(data, output_dir):
    valid = data[data["N_POINTS"].gt(0)]
    figure, axis = plt.subplots(figsize=(8, 5))
    axis.hist(valid["N_POINTS"], bins=60, color="slateblue")
    axis.set_title("Number of points per trip")
    axis.set_xlabel("Points")
    axis.set_ylabel("Number of trips")
    save_figure(figure, output_dir, "06_points_per_trip.png")

    duration_minutes = valid["DURATION_S"].div(60)
    duration_limit = duration_minutes.quantile(0.99)
    figure, axis = plt.subplots(figsize=(8, 5))
    axis.hist(duration_minutes[duration_minutes < duration_limit], bins=60, color="seagreen")
    axis.set_title("Trip duration (excluding 99th percentile)")
    axis.set_xlabel("Minutes")
    save_figure(figure, output_dir, "07_trip_duration.png")

    figure, axis = plt.subplots(figsize=(8, 5))
    axis.hist(duration_minutes[duration_minutes >= duration_limit], bins=40, color="olivedrab")
    axis.set_title("Trip duration: upper 1%")
    axis.set_xlabel("Minutes")
    save_figure(figure, output_dir, "08_trip_duration_upper.png")

    distance_km = valid["DISTANCE_M"].div(1000)
    distance_limit = distance_km.quantile(0.99)
    figure, axis = plt.subplots(figsize=(8, 5))
    axis.hist(distance_km[distance_km < distance_limit], bins=60, color="firebrick")
    axis.set_title("Trip distance (excluding 99th percentile)")
    axis.set_xlabel("Kilometers")
    save_figure(figure, output_dir, "09_trip_distance.png")

    figure, axis = plt.subplots(figsize=(8, 5))
    axis.hist(distance_km[distance_km >= distance_limit], bins=40, color="darkred")
    axis.set_title("Trip distance: upper 1%")
    axis.set_xlabel("Kilometers")
    save_figure(figure, output_dir, "10_trip_distance_upper.png")

    speeds = [speed for values in valid["SEGMENT_SPEEDS_KMH"] for speed in values]
    figure, axis = plt.subplots(figsize=(9, 5))
    if speeds:
        series = pd.Series(speeds)
        axis.hist(series[series.le(series.quantile(0.99))], bins=70, color="darkorange")
    axis.set_title("Speeds between consecutive points")
    axis.set_xlabel("km/h")
    axis.set_ylabel("Count")
    save_figure(figure, output_dir, "14_segment_distributions.png")

    figure, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    speed_series = pd.Series(speeds)
    distance_series = pd.Series(
        [distance for values in valid["SEGMENT_DISTANCES_M"] for distance in values]
    )
    for axis, values, title, color, unit in [
        (axes[0], speed_series, "Speeds: upper 1%", "darkorange", "km/h"),
        (axes[1], distance_series, "Distances between points: upper 1%", "firebrick", "meters"),
    ]:
        limit = values.quantile(0.99) if not values.empty else 0
        axis.hist(values[values >= limit], bins=40, color=color)
        axis.set_title(title)
        axis.set_xlabel(unit)
    save_figure(figure, output_dir, "15_segment_distributions_upper.png")

    starts = valid["POINTS"].map(lambda points: points[0] if points else None).dropna()
    if not starts.empty:
        coordinates = pd.DataFrame(starts.tolist(), columns=["LONGITUDE", "LATITUDE"])
        figure, axis = plt.subplots(figsize=(8, 8))
        axis.hexbin(
            coordinates["LONGITUDE"], coordinates["LATITUDE"],
            gridsize=80, bins="log", cmap="inferno",
        )
        axis.set_title("Starting point density")
        axis.set_xlabel("Longitude")
        axis.set_ylabel("Latitude")
        save_figure(figure, output_dir, "11_start_points_density.png")

    figure, axes = plt.subplots(1, 2, figsize=(14, 5))
    axes[0].hist(valid["N_POINTS"], bins=60, color="slateblue")
    axes[0].set_title("Number of points distribution")
    axes[0].set_xlabel("Points")
    if speeds:
        speed_series = pd.Series(speeds)
        axes[1].hist(speed_series[speed_series.le(speed_series.quantile(0.99))], bins=60, color="darkorange")
    axes[1].set_title("Speed distribution")
    axes[1].set_xlabel("km/h")
    save_figure(figure, output_dir, "12_quality_overview.png")

    segment_distances = [
        distance for values in valid["SEGMENT_DISTANCES_M"] for distance in values
    ]
    figure, axes = plt.subplots(1, 2, figsize=(14, 5))
    if speeds:
        axes[0].hist(speeds, bins=60, color="darkorange")
    axes[0].set_title("Speeds between points")
    axes[0].set_xlabel("km/h")
    if segment_distances:
        axes[1].hist(segment_distances, bins=60, color="teal")
    axes[1].set_title("Distances between points")
    axes[1].set_xlabel("Meters")
    save_figure(figure, output_dir, "13_speed_and_point_gaps.png")


def detect_suspicious(data, output_dir, speed_limit):
    data = data.copy()
    data["HAS_LARGE_JUMP"] = data["MAX_SPEED_KMH"].gt(speed_limit)
    data["IS_GHOST"] = data["DISTANCE_M"].le(100)

    print(
        f"\nTrips with large jumps (> {speed_limit:g} km/h): "
        f"{int(data['HAS_LARGE_JUMP'].sum()):,}"
    )
    print(f"Ghost trips (<= 100 m): {int(data['IS_GHOST'].sum()):,}")

    figure, axis = plt.subplots(figsize=(9, 5))
    for call_type, group in data.groupby("CALL_TYPE"):
        axis.hist(group["DISTANCE_M"].div(1000), bins=60, alpha=0.55, label=call_type)
    axis.set_xlim(left=0)
    axis.set_title("Distance distribution by call type")
    axis.set_xlabel("Kilometers")
    axis.legend(title="CALL_TYPE")
    save_figure(figure, output_dir, "16_distances_by_call_type.png")

    figure, axis = plt.subplots(figsize=(9, 5))
    axis.hist(data.loc[data["IS_GHOST"], "DISTANCE_M"], bins=40, color="mediumpurple")
    axis.set_title("Ghost trips")
    axis.set_xlabel("Total distance (meters)")
    axis.set_ylabel("Count")
    save_figure(figure, output_dir, "17_ghost_trips.png")

    figure, axis = plt.subplots(figsize=(9, 5))
    for call_type, group in data.groupby("CALL_TYPE"):
        axis.hist(
            group.loc[group["IS_GHOST"], "DISTANCE_M"],
            bins=30, alpha=0.55, label=call_type,
        )
    axis.set_title("Ghost trip distance by call type")
    axis.set_xlabel("Total distance (meters)")
    axis.legend(title="CALL_TYPE")
    save_figure(figure, output_dir, "18_ghost_distance_by_type.png")

    figure, axis = plt.subplots(figsize=(9, 5))
    jump_speeds = data.loc[data["HAS_LARGE_JUMP"], "MAX_SPEED_KMH"]
    axis.hist(jump_speeds, bins=50, color="firebrick")
    axis.axvline(speed_limit, color="black", linestyle="--", label=f"Threshold {speed_limit:g} km/h")
    axis.set_title("Detected speed spikes")
    axis.set_xlabel("Maximum speed (km/h)")
    axis.legend()
    save_figure(figure, output_dir, "19_velocity_spikes.png")

    figure, axis = plt.subplots(figsize=(9, 8))
    for points in data.loc[data["HAS_LARGE_JUMP"], "POINTS"].head(200):
        if len(points) >= 2:
            coordinates = pd.DataFrame(points, columns=["LONGITUDE", "LATITUDE"])
            axis.plot(coordinates["LONGITUDE"], coordinates["LATITUDE"], alpha=0.35, color="firebrick")
    axis.set_title("Trajectories with large GPS jumps")
    axis.set_xlabel("Longitude")
    axis.set_ylabel("Latitude")
    save_figure(figure, output_dir, "20_suspicious_trajectories.png")


def correct_anchor_based(data, output_dir, speed_limit, max_run=4):
    corrections = []
    long_runs = []
    corrected_paths = []

    for row in data.itertuples(index=False):
        points = row.POINTS
        if len(points) < 3:
            continue
        corrected = [list(point) for point in points]
        anchor = 0
        run_start = None
        for point_index in range(1, len(points)):
            elapsed = (point_index - anchor) * POINT_INTERVAL_SECONDS
            distance = haversine(
                (points[anchor][1], points[anchor][0]),
                (points[point_index][1], points[point_index][0]),
                unit=Unit.METERS,
            )
            speed = distance / elapsed * 3.6 if elapsed else 0
            if speed > speed_limit:
                run_start = anchor + 1 if run_start is None else run_start
                if point_index - anchor > max_run:
                    long_runs.append((row.TRIP_ID, run_start, point_index))
                    anchor = point_index
                    run_start = None
                continue

            if point_index - anchor > 1:
                run = list(range(anchor + 1, point_index))
                if len(run) <= max_run:
                    for offset, index in enumerate(run, start=1):
                        fraction = offset / (len(run) + 1)
                        corrected[index] = [
                            points[anchor][0] + fraction * (points[point_index][0] - points[anchor][0]),
                            points[anchor][1] + fraction * (points[point_index][1] - points[anchor][1]),
                        ]
                    corrections.append({
                        "TRIP_ID": row.TRIP_ID,
                        "N_CORRECTIONS": len(run),
                        "POLYLINE_ORIGINAL": repr(points),
                        "POLYLINE_CORRECTED": repr(corrected),
                    })
                    if len(corrected_paths) < 200:
                        corrected_paths.append((points, corrected))
                else:
                    long_runs.append((row.TRIP_ID, run[0], run[-1]))
            anchor = point_index
            run_start = None

    for filename, title, corrected_color in [
        ("21_corrected_trajectories.png", "Corrected trajectories", "seagreen"),
        ("22_anchor_based_trajectories.png", "Anchor-based corrections", "tab:blue"),
    ]:
        figure, axis = plt.subplots(figsize=(9, 8))
        for original, corrected_path in corrected_paths:
            axis.plot(
                [point[0] for point in original], [point[1] for point in original],
                color="0.5", alpha=0.3, linestyle="--",
            )
            axis.plot(
                [point[0] for point in corrected_path], [point[1] for point in corrected_path],
                color=corrected_color, alpha=0.5,
            )
        axis.set_title(title)
        axis.set_xlabel("Longitude")
        axis.set_ylabel("Latitude")
        save_figure(figure, output_dir, filename)
    print(f"Anchor-based corrections: {len(corrections):,} trips")
    print(f"Runs that are too long: {len(long_runs):,}")


def print_summary(metadata, sample):
    call_counts = metadata["CALL_TYPE"].value_counts()
    rows = [
        ["Trips", f"{len(metadata):,}"],
        ["Taxis", f"{metadata['TAXI_ID'].nunique():,}"],
        ["Type A trips", f"{call_counts.get('A', 0):,}"],
        ["Type B trips", f"{call_counts.get('B', 0):,}"],
        ["Type C trips", f"{call_counts.get('C', 0):,}"],
        ["Average points (sample)", f"{sample['N_POINTS'].mean():.1f}"],
        ["Median distance (sample)", f"{sample['DISTANCE_M'].median() / 1000:.2f} km"],
    ]
    print("\nSummary")
    print(tabulate(rows, headers=["Metric", "Value"], tablefmt="github"))


def parse_args():
    parser = argparse.ArgumentParser(description="Analyses du dataset Porto Taxi")
    parser.add_argument("csv_path")
    parser.add_argument("--outdir", default="figures_clean")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--chunksize", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--speed-limit", type=float, default=100)
    parser.add_argument(
        "--analysis", default="all",
        help="all, metadata, consistency, quality, suspicious, correction, summary",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    selected = {item.strip().lower() for item in args.analysis.split(",")}
    if "all" in selected:
        selected = {"metadata", "consistency", "quality", "suspicious", "correction", "summary"}

    os.makedirs(args.outdir, exist_ok=True)
    metadata = None
    sample = None

    if selected & {"metadata", "consistency", "summary"}:
        metadata = read_metadata(args.csv_path, args.chunksize)
    if selected & {"quality", "suspicious", "correction", "summary"}:
        sample = read_polyline_sample(args.csv_path, args.sample)
        sample = add_metrics(sample)

    if "metadata" in selected:
        plot_metadata(metadata, args.outdir)
        plot_time_distributions(metadata, args.outdir)
    if "consistency" in selected:
        report_consistency(metadata, args.outdir)
    if "quality" in selected:
        plot_quality(sample, args.outdir)
    if "suspicious" in selected:
        detect_suspicious(sample, args.outdir, args.speed_limit)
    if "correction" in selected:
        correct_anchor_based(sample, args.outdir, args.speed_limit)
    if "summary" in selected:
        print_summary(metadata, sample)

    print(f"\nFinished. Results saved to: {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()