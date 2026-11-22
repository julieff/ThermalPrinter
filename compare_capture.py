import argparse
from pathlib import Path


WIDTH = 384
HEIGHT = 28


def parse_args():
    parser = argparse.ArgumentParser(
        description="Split MXW01 0x000F raster writes into separate print jobs."
    )
    parser.add_argument("capture_csv")
    parser.add_argument("--output-dir", default="captured-jobs")
    return parser.parse_args()


def parse_hex(value):
    value = "".join(value.split()).replace("0x", "").replace(":", "").replace("-", "")
    if len(value) % 2:
        raise ValueError("Incomplete hexadecimal payload")
    return bytes.fromhex(value)


def load_rows(path):
    rows = []
    with open(path, "r", encoding="ascii") as capture:
        for line_number, line in enumerate(capture, start=1):
            line = line.strip()
            if not line:
                continue
            separator = "\t" if "\t" in line else ","
            fields = [field.strip().strip('"') for field in line.split(separator)]
            if len(fields) < 5:
                continue
            frame, time, opcode, handle, value = fields[:5]

            if opcode.lower() not in ("0x52", "82"):
                continue
            try:
                payload = parse_hex(value)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid 0x000F payload on line {line_number}: {exc}"
                ) from exc
            rows.append((frame, time, handle.lower(), payload))
    return rows


def split_jobs(rows):
    jobs = []
    current = None
    for frame, time, handle, payload in rows:
        if handle in ("0x000a", "000a") and payload.startswith(b"\x22\x21\xa9"):
            if current:
                jobs.append(current)
            current = []
        elif current is not None and handle in ("0x000f", "000f"):
            current.append(payload)
    if current:
        jobs.append(current)

    return [
        b"".join(job)
        for job in jobs
        if len(b"".join(job)) == (WIDTH // 8) * HEIGHT
    ]


def raster_to_rows(raster, lsb=False):
    rows = []
    for y in range(HEIGHT):
        row = []
        for x in range(WIDTH):
            byte = raster[y * (WIDTH // 8) + x // 8]
            bit = x % 8
            if lsb:
                row.append(bool(byte & (1 << bit)))
            else:
                row.append(bool(byte & (1 << (7 - bit))))
        rows.append(row)
    return rows


def write_pbm(path, rows):
    with open(path, "w", encoding="ascii") as output:
        output.write(f"P1\n{WIDTH} {HEIGHT}\n")
        for row in rows:
            output.write(" ".join("1" if pixel else "0" for pixel in row))
            output.write("\n")


def ink_bounds(rows):
    xs = [
        x
        for y in range(HEIGHT)
        for x in range(WIDTH)
        if rows[y][x]
    ]
    ys = [
        y
        for y in range(HEIGHT)
        for x in range(WIDTH)
        if rows[y][x]
    ]
    if not xs:
        return None
    return min(xs), max(xs), min(ys), max(ys)


def main():
    args = parse_args()
    rows = load_rows(args.capture_csv)
    jobs = split_jobs(rows)
    if not jobs:
        raise SystemExit("No complete 1344-byte 0x000F jobs found")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for job_number, raster in enumerate(jobs, start=1):
        output_dir.joinpath(f"job-{job_number}.hex").write_text(
            raster.hex().upper(),
            encoding="ascii",
        )
        for lsb in (False, True):
            name = "lsb" if lsb else "msb"
            preview = raster_to_rows(raster, lsb=lsb)
            write_pbm(
                output_dir / f"job-{job_number}-{name}.pbm",
                preview,
            )
            print(
                f"Job {job_number} {name}: "
                f"{len(raster)} bytes, ink bounds {ink_bounds(preview)}"
            )


if __name__ == "__main__":
    main()
