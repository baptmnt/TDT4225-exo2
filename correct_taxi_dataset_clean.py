"""Create an auditable corrected Porto Taxi CSV.

The source file is read but never modified. Corrections are limited to short
anchor-based GPS outlier runs. The output keeps the original columns and adds
audit columns describing every correction or unresolved case.
"""

import argparse
import ast
import csv
import os
from time import monotonic

import pandas as pd
from haversine import Unit, haversine


POINT_INTERVAL_SECONDS = 15
DEFAULT_CHUNKSIZE = 50_000
DEFAULT_SPEED_THRESHOLD = 150.0
DEFAULT_MIN_DEVIATION = 500.0
DEFAULT_MIN_ANCHOR_DISTANCE = 1_000.0
DEFAULT_MAX_RUN = 10
DEFAULT_ORIGIN_STAND_DISTANCE = 50.0


def parse_points(value):
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        points = ast.literal_eval(value)
    except (SyntaxError, ValueError, TypeError):
        return []
    return points if isinstance(points, list) else []


def distance_m(first_point, second_point):
    return haversine(
        (first_point[1], first_point[0]),
        (second_point[1], second_point[0]),
        unit=Unit.METERS,
    )


def total_distance_m(points):
    return sum(distance_m(first, second) for first, second in zip(points, points[1:]))


def max_speed_kmh(points):
    distances = [distance_m(first, second) for first, second in zip(points, points[1:])]
    return max((distance / POINT_INTERVAL_SECONDS * 3.6 for distance in distances), default=0.0)


def interpolate_between(points, anchor_index, return_index):
    run = list(range(anchor_index + 1, return_index))
    span = len(run) + 1
    candidates = []
    for offset in range(1, span):
        fraction = offset / span
        candidates.append((
            points[anchor_index][0]
            + fraction * (points[return_index][0] - points[anchor_index][0]),
            points[anchor_index][1]
            + fraction * (points[return_index][1] - points[anchor_index][1]),
        ))
    return run, candidates


def correct_polyline(points, args):
    """Correct accepted short runs and return audit information."""
    if len(points) < 3:
        return points, [], [], False

    corrected = list(points)
    anchor_index = 0
    cursor = 1
    corrected_indices = []
    unresolved_runs = []
    suspicious = False

    while cursor < len(points):
        elapsed = (cursor - anchor_index) * POINT_INTERVAL_SECONDS
        anchor_distance = distance_m(points[anchor_index], points[cursor])
        anchor_speed = anchor_distance / elapsed * 3.6

        if anchor_speed > args.speed_threshold:
            suspicious = True
            if cursor - anchor_index > args.max_run:
                unresolved_runs.append((anchor_index + 1, cursor))
                anchor_index = cursor
            cursor += 1
            continue

        run, candidates = interpolate_between(points, anchor_index, cursor)
        if run:
            deviations = [
                distance_m(original, candidate)
                for original, candidate in zip(
                    (points[index] for index in run), candidates
                )
            ]
            should_correct = (
                len(run) <= args.max_run
                and anchor_distance >= args.anchor_min_distance
                and max(deviations, default=0.0) >= args.anchor_min_deviation
            )
            if should_correct:
                for index, candidate in zip(run, candidates):
                    corrected[index] = candidate
                corrected_indices.extend(run)
                suspicious = True
            elif (
                len(run) > args.max_run
                or max(deviations, default=0.0) >= args.anchor_min_deviation
            ):
                unresolved_runs.append((run[0], run[-1]))
                suspicious = True
        anchor_index = cursor
        cursor += 1

    if max_speed_kmh(corrected) > max_speed_kmh(points) + 1e-6:
        return points, [], unresolved_runs, suspicious
    return corrected, corrected_indices, unresolved_runs, suspicious


def collect_stand_centers(input_path, chunksize):
    """Compute the mean first point for every known B origin stand."""
    sums = {}
    columns = ["CALL_TYPE", "ORIGIN_STAND", "POLYLINE"]
    for chunk in pd.read_csv(input_path, usecols=columns, chunksize=chunksize):
        for row in chunk[chunk["CALL_TYPE"].eq("B")].itertuples(index=False):
            if pd.isna(row.ORIGIN_STAND):
                continue
            stand = str(row.ORIGIN_STAND).strip()
            points = parse_points(row.POLYLINE)
            if not stand or not points:
                continue
            longitude, latitude = points[0]
            total_lon, total_lat, count = sums.get(stand, (0.0, 0.0, 0))
            sums[stand] = (total_lon + longitude, total_lat + latitude, count + 1)
    return {
        stand: (total_lon / count, total_lat / count)
        for stand, (total_lon, total_lat, count) in sums.items()
    }


def collect_rows_to_keep(input_path, chunksize):
    """Keep the earliest non-null timestamp for each TRIP_ID."""
    earliest = {}
    counts = {}
    row_index = 0
    for chunk in pd.read_csv(input_path, usecols=["TRIP_ID", "TIMESTAMP"], chunksize=chunksize):
        for row in chunk.itertuples(index=False):
            trip_id = row.TRIP_ID
            current_index = row_index
            row_index += 1
            if pd.isna(trip_id):
                continue
            counts[trip_id] = counts.get(trip_id, 0) + 1
            timestamp = pd.to_numeric(row.TIMESTAMP, errors="coerce")
            key = (pd.isna(timestamp), timestamp if not pd.isna(timestamp) else 0, current_index)
            if trip_id not in earliest or key < earliest[trip_id][0]:
                earliest[trip_id] = (key, current_index)
    duplicate_groups = sum(count > 1 for count in counts.values())
    duplicate_rows = sum(count - 1 for count in counts.values() if count > 1)
    return {trip_id: value[1] for trip_id, value in earliest.items()}, duplicate_groups, duplicate_rows


def infer_origin_stand(row, points, stand_centers, max_distance):
    original = row.get("ORIGIN_STAND")
    is_missing = pd.isna(original) or not str(original).strip()
    if str(row.get("CALL_TYPE", "")).strip() != "B" or not is_missing:
        return original, "unchanged"
    if not points or not stand_centers:
        return original, "missing_no_reference"

    stand, distance = min(
        (
            (stand, distance_m(points[0], center))
            for stand, center in stand_centers.items()
        ),
        key=lambda item: item[1],
    )
    if distance <= max_distance:
        return stand, "reconstructed"
    return original, "missing_too_far"


def should_drop(row, points, args):
    missing_data = str(row.get("MISSING_DATA", "")).strip().lower() == "true"
    if missing_data and not args.keep_missing_data:
        return "missing_data"
    if not args.keep_ghost_trips and total_distance_m(points) <= args.ghost_distance_threshold:
        return "ghost_trip"
    return None


def process_dataset(args, rows_to_keep, stand_centers):
    first_output_chunk = True
    source_index = 0
    chunk_number = 0
    start_time = monotonic()
    totals = {
        "written": 0, "corrected_trips": 0, "corrected_points": 0,
        "suspicious": 0, "unresolved_runs": 0, "reconstructed_stands": 0,
        "dropped_duplicates": 0, "dropped_missing_data": 0,
        "dropped_ghosts": 0, "dropped_origin_stands": 0,
    }
    audit_columns = [
        "CORRECTION_STATUS", "SUSPICIOUS_TRIP", "CORRECTED_POINT_INDICES",
        "N_CORRECTED_POINTS", "N_UNRESOLVED_RUNS", "ORIGIN_STAND_CORRECTION",
        "SOURCE_ROW_INDEX",
    ]

    for chunk in pd.read_csv(args.input, chunksize=args.chunksize):
        chunk_number += 1
        source_rows = list(chunk.to_dict(orient="records"))
        output_rows = []
        original_columns = list(chunk.columns)
        for row in source_rows:
            current_index = source_index
            source_index += 1
            trip_id = row.get("TRIP_ID")
            if not pd.isna(trip_id) and rows_to_keep.get(trip_id) != current_index:
                totals["dropped_duplicates"] += 1
                continue

            points = parse_points(row.get("POLYLINE"))
            drop_reason = should_drop(row, points, args)
            if drop_reason == "missing_data":
                totals["dropped_missing_data"] += 1
                continue
            if drop_reason == "ghost_trip":
                totals["dropped_ghosts"] += 1
                continue

            corrected, indices, unresolved, suspicious = correct_polyline(points, args)
            new_stand, stand_status = infer_origin_stand(
                row, points, stand_centers, args.origin_stand_max_distance
            )
            unresolved_stand = (
                str(row.get("CALL_TYPE", "")).strip() == "B"
                and (pd.isna(new_stand) or not str(new_stand).strip())
            )
            if unresolved_stand and not args.keep_unresolved_origin_stand:
                totals["dropped_origin_stands"] += 1
                continue

            row["ORIGIN_STAND"] = new_stand
            row["POLYLINE"] = repr(corrected) if indices else row.get("POLYLINE", "")
            row["CORRECTION_STATUS"] = "corrected" if indices else (
                "suspicious_unresolved" if suspicious else "unchanged"
            )
            row["SUSPICIOUS_TRIP"] = bool(suspicious)
            row["CORRECTED_POINT_INDICES"] = ",".join(map(str, indices))
            row["N_CORRECTED_POINTS"] = len(indices)
            row["N_UNRESOLVED_RUNS"] = len(unresolved)
            row["ORIGIN_STAND_CORRECTION"] = stand_status
            row["SOURCE_ROW_INDEX"] = current_index
            output_rows.append(row)

            totals["written"] += 1
            totals["corrected_trips"] += bool(indices)
            totals["corrected_points"] += len(indices)
            totals["suspicious"] += bool(suspicious)
            totals["unresolved_runs"] += len(unresolved)
            totals["reconstructed_stands"] += stand_status == "reconstructed"

        if output_rows:
            output = pd.DataFrame(output_rows)
            output = output[[column for column in original_columns if column in output] + audit_columns]
            output.to_csv(
                args.output, mode="w" if first_output_chunk else "a",
                header=first_output_chunk, index=False, quoting=csv.QUOTE_MINIMAL,
            )
            first_output_chunk = False
        elapsed_minutes = (monotonic() - start_time) / 60
        print(
            f"  Chunk {chunk_number}: {source_index:,} lignes parcourues, "
            f"{totals['written']:,} conservées, "
            f"{elapsed_minutes:.1f} min écoulées",
            flush=True,
        )
    return totals


def print_summary(totals, duplicate_groups, duplicate_rows, stand_count, output):
    print("\nRésumé de la correction")
    print(f"Références ORIGIN_STAND: {stand_count:,}")
    print(f"Groupes TRIP_ID dupliqués: {duplicate_groups:,} ({duplicate_rows:,} lignes en trop)")
    print(f"Lignes conservées: {totals['written']:,}")
    print(f"Trajets corrigés: {totals['corrected_trips']:,}")
    print(f"Points corrigés: {totals['corrected_points']:,}")
    print(f"ORIGIN_STAND reconstruits: {totals['reconstructed_stands']:,}")
    print(f"Trajets suspects: {totals['suspicious']:,}")
    print(f"Runs non résolus: {totals['unresolved_runs']:,}")
    print(f"Doublons supprimés: {totals['dropped_duplicates']:,}")
    print(f"MISSING_DATA supprimés: {totals['dropped_missing_data']:,}")
    print(f"Ghost trips supprimés: {totals['dropped_ghosts']:,}")
    print(f"B sans ORIGIN_STAND supprimés: {totals['dropped_origin_stands']:,}")
    print(f"[saved] {os.path.abspath(output)}")


def parse_args():
    parser = argparse.ArgumentParser(description="Correct and audit a Porto Taxi CSV")
    parser.add_argument("input", help="Source CSV")
    parser.add_argument("output", help="Destination CSV")
    parser.add_argument("--chunksize", type=int, default=DEFAULT_CHUNKSIZE)
    parser.add_argument("--speed-threshold", type=float, default=DEFAULT_SPEED_THRESHOLD)
    parser.add_argument("--anchor-min-deviation", type=float, default=DEFAULT_MIN_DEVIATION)
    parser.add_argument("--anchor-min-distance", type=float, default=DEFAULT_MIN_ANCHOR_DISTANCE)
    parser.add_argument("--max-run", type=int, default=DEFAULT_MAX_RUN)
    parser.add_argument("--origin-stand-max-distance", type=float, default=DEFAULT_ORIGIN_STAND_DISTANCE)
    parser.add_argument("--ghost-distance-threshold", type=float, default=100)
    parser.add_argument("--keep-missing-data", action="store_true")
    parser.add_argument("--keep-ghost-trips", action="store_true")
    parser.add_argument("--keep-unresolved-origin-stand", action="store_true")
    args = parser.parse_args()
    if args.chunksize <= 0 or args.max_run <= 0:
        parser.error("--chunksize et --max-run doivent être positifs")
    return args


def main():
    args = parse_args()
    print("[1/3] Collecte des références ORIGIN_STAND...", flush=True)
    stand_centers = collect_stand_centers(args.input, args.chunksize)
    print(f"  -> {len(stand_centers):,} stands de référence", flush=True)
    print("[2/3] Recherche des doublons TRIP_ID...", flush=True)
    rows_to_keep, duplicate_groups, duplicate_rows = collect_rows_to_keep(
        args.input, args.chunksize
    )
    print(
        f"  -> {duplicate_groups:,} groupes dupliqués, "
        f"{duplicate_rows:,} lignes en trop",
        flush=True,
    )
    print("[3/3] Correction et écriture du fichier...", flush=True)
    totals = process_dataset(args, rows_to_keep, stand_centers)
    print_summary(
        totals, duplicate_groups, duplicate_rows, len(stand_centers), args.output
    )


if __name__ == "__main__":
    main()