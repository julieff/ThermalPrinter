import argparse
import asyncio
from pathlib import Path


WIDTH = 384
HEIGHT = 28
AE01 = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02 = "0000ae02-0000-1000-8000-00805f9b34fb"
AE03 = "0000ae03-0000-1000-8000-00805f9b34fb"
AE04 = "0000ae04-0000-1000-8000-00805f9b34fb"
AE05 = "0000ae05-0000-1000-8000-00805f9b34fb"
AE3C = "0000ae3c-0000-1000-8000-00805f9b34fb"

CONTROL_PREFIX = (
    bytes.fromhex("2221A70000000000"),
    bytes.fromhex("2221B10001000000FF"),
    bytes.fromhex("2221A10001000000FF"),
    bytes.fromhex("2221A20001005D94FF"),
    bytes.fromhex("2221A20001006229FF"),
)
PAGE_START_TEMPLATE = bytes.fromhex("2221A90004001C0030000000")
CONTROL_SUFFIX = bytes.fromhex("2221AD000100000000")

def parse_args():
    parser = argparse.ArgumentParser(
        description="Print text to an MXW01 Bluetooth thermal printer."
    )
    parser.add_argument("text")
    parser.add_argument("--name", default="MXW01")
    parser.add_argument("--address")
    parser.add_argument("--font-size", type=int, default=24)
    parser.add_argument("--margin", type=int, default=8)
    parser.add_argument("--delay-ms", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--transform",
        choices=("none", "mirror-x", "mirror-y", "rotate-180", "lsb"),
        default="none",
    )
    parser.add_argument(
        "--preview",
        help="Write the generated 384x28 PBM preview and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate the raster without discovering or writing to BLE",
    )
    parser.add_argument(
        "--raster-file",
        help="Write the 1344-byte raster as uppercase hexadecimal",
    )
    parser.add_argument(
        "--known-raster",
        choices=("hello", "abc"),
        help="Replay the exact captured app raster for one known text job",
    )
    parser.add_argument(
        "--font",
        help="Optional TrueType font path",
    )
    parser.add_argument(
        "--packing",
        choices=("msb", "lsb"),
        default="lsb",
        help="Bit packing within each raster byte",
    )
    parser.add_argument(
        "--invert",
        action="store_true",
        help="Invert foreground/background pixels",
    )
    parser.add_argument(
        "--line-height",
        type=int,
        default=24,
        help="Vertical spacing between rendered text lines",
    )
    return parser.parse_args()


def find_font(font_path, font_size):
    if font_path:
        return ImageFont.truetype(font_path, font_size)

    candidates = (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, font_size)
    return ImageFont.load_default()


def wrap_text(text, font, margin):
    if "\n" not in text:
        text = " ".join(text.split())
    lines = []
    available_width = WIDTH - 2 * margin

    for explicit_line in text.splitlines() or [""]:
        if not explicit_line:
            lines.append("")
            continue

        words = explicit_line.split()
        if not words:
            lines.append("")
            continue

        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if ImageDraw.Draw(Image.new("1", (1, 1), 1)).textlength(candidate, font=font) <= available_width:
                current = candidate
            else:
                lines.append(current)
                current = word

        while current:
            bbox = ImageDraw.Draw(Image.new("1", (1, 1), 1)).textbbox((0, 0), current, font=font)
            if bbox[2] - bbox[0] <= available_width:
                break
            split_at = len(current)
            while split_at > 1:
                probe = current[:split_at]
                bbox = ImageDraw.Draw(Image.new("1", (1, 1), 1)).textbbox((0, 0), probe, font=font)
                if bbox[2] - bbox[0] <= available_width:
                    break
                split_at -= 1
            lines.append(current[:split_at])
            current = current[split_at:].lstrip()
        if current:
            lines.append(current)
    return lines


def render_text(text, font_size, margin, font_path=None, line_height=24):
    if not 0 <= margin < WIDTH:
        raise ValueError("Margin must be between 0 and 383")
    if font_size < 8:
        raise ValueError("Font size must be at least 8")
    if line_height < 1:
        raise ValueError("Line height must be at least 1")

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise SystemExit(
            "Missing Pillow. Install it with: python -m pip install Pillow"
        ) from exc

    globals()["Image"] = Image
    globals()["ImageDraw"] = ImageDraw
    globals()["ImageFont"] = ImageFont

    font = find_font(font_path, font_size)
    lines = wrap_text(text, font, margin)
    return lines


def render_tall_text(text, font_size, margin, font_path=None, line_height=24):
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise SystemExit(
            "Missing Pillow. Install it with: python -m pip install Pillow"
        ) from exc

    lines = render_text(text, font_size, margin, font_path, line_height)
    font = find_font(font_path, font_size)
    height = max(HEIGHT, 2 + len(lines) * line_height)
    image = Image.new("1", (WIDTH, height), 1)
    draw = ImageDraw.Draw(image)
    y = 2
    for line in lines:
        draw.text((margin, y), line, fill=0, font=font)
        y += line_height

    rows = [
        [image.getpixel((x, y)) == 0 for x in range(WIDTH)]
        for y in range(height)
    ]
    return rows, height


def transform_raster(rows, transform):
    if transform == "mirror-x":
        return [list(reversed(row)) for row in rows]
    if transform == "mirror-y":
        return list(reversed(rows))
    if transform == "rotate-180":
        return [list(reversed(row)) for row in reversed(rows)]
    return [row[:] for row in rows]


def rows_to_raster(rows, packing="msb", invert=False, height=None):
    raster_height = height if height is not None else len(rows)
    raster = bytearray()
    for row in rows[:raster_height]:
        for x in range(0, WIDTH, 8):
            value = 0
            for bit in range(8):
                pixel = row[x + bit]
                if invert:
                    pixel = not pixel
                if pixel:
                    value |= (1 << bit) if packing == "lsb" else (1 << (7 - bit))
            raster.append(value)
    return bytes(raster)


def build_page_start(raster_height):
    if not 1 <= raster_height <= 0xFFFF:
        raise ValueError("Raster height must fit in an unsigned 16-bit field")
    command = bytearray(PAGE_START_TEMPLATE)
    command[6:8] = raster_height.to_bytes(2, "little")
    return bytes(command)


def write_pbm(path, rows):
    with open(path, "w", encoding="ascii") as output:
        output.write(f"P1\n384 {len(rows)}\n")
        for row in rows:
            output.write(" ".join("1" if pixel else "0" for pixel in row))
            output.write("\n")


async def main():
    args = parse_args()
    if args.font_size < 8:
        raise SystemExit("--font-size must be at least 8")
    if args.delay_ms < 0:
        raise SystemExit("--delay-ms must not be negative")

    if args.known_raster:
        job_number = 1 if args.known_raster == "hello" else 2
        hex_path = Path(__file__).with_name("captured-jobs") / f"job-{job_number}.hex"
        try:
            raster = bytes.fromhex(hex_path.read_text(encoding="ascii"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Could not load known raster: {exc}") from exc
        rows = []
        for y in range(HEIGHT):
            row = []
            for x in range(WIDTH):
                byte = raster[y * (WIDTH // 8) + x // 8]
                row.append(bool(byte & (1 << (7 - x % 8))))
            rows.append(row)
        page_rows = [rows]
        rasters = [raster]
    else:
        rows, raster_height = render_tall_text(
            args.text,
            args.font_size,
            args.margin,
            args.font,
            args.line_height,
        )
        rows = transform_raster(rows, args.transform)
        raster = rows_to_raster(
            rows,
            packing=args.packing,
            invert=args.invert,
            height=raster_height,
        )
        rasters = [raster]

    if args.preview:
        write_pbm(args.preview, rows)
        print(f"Wrote preview: {args.preview}")

    if args.raster_file:
        with open(args.raster_file, "w", encoding="ascii") as raster_output:
            for page_raster in rasters:
                for offset in range(0, len(page_raster), 480):
                    chunk = page_raster[offset:offset + 480]
                    raster_output.write(chunk.hex().upper())
                    raster_output.write("\n")
        print(f"Wrote raster: {args.raster_file}")

    if args.preview or args.raster_file or args.dry_run:
        print(
            f"Raster: {len(rasters)} job(s), {len(raster)} bytes, "
            f"384x{len(raster) // (WIDTH // 8)} pixels"
        )
        return

    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise SystemExit("Missing Bleak. Install it with: python -m pip install bleak") from exc

    device = None
    if args.address:
        device = await BleakScanner.find_device_by_address(args.address, timeout=args.timeout)
    else:
        devices = await BleakScanner.discover(timeout=args.timeout)
        device = next(
            (
                item
                for item in devices
                if item.name and args.name.lower() in item.name.lower()
            ),
            None,
        )
    if device is None:
        raise SystemExit(f"Printer not found: {args.address or args.name}")

    async with BleakClient(device) as client:
        print(f"Connected: {device.name or 'unknown'} ({device.address})")
        characteristics = {}
        notify_characteristics = {}
        for service in client.services:
            for characteristic in service.characteristics:
                uuid = characteristic.uuid.lower()
                characteristics[uuid] = characteristic
                properties = {item.lower() for item in characteristic.properties}
                if "notify" in properties or "indicate" in properties:
                    notify_characteristics[uuid] = characteristic

        def notification_handler(sender, data):
            print(f"Notification: {data.hex().upper()}")

        subscribed = []
        for uuid in (AE02, AE04, AE05, AE3C):
            characteristic = notify_characteristics.get(uuid)
            if characteristic is not None:
                await client.start_notify(characteristic.uuid, notification_handler)
                subscribed.append(characteristic.uuid)

        handle_to_uuid = {0x000A: AE01, 0x000F: AE03}
        print(f"Printing {len(rasters)} raster job(s)")

        async def send_packet(handle, payload):
            characteristic = characteristics.get(handle_to_uuid[handle])
            if characteristic is None:
                raise SystemExit(
                    f"Characteristic not found: {handle_to_uuid[handle]}"
                )
            properties = {item.lower() for item in characteristic.properties}
            await client.write_gatt_char(
                characteristic.uuid,
                bytes(payload),
                response="write" in properties,
            )

        for index, payload in enumerate(CONTROL_PREFIX, start=1):
            await send_packet(0x000A, payload)
            print(
                f"  prefix packet {index}/{len(CONTROL_PREFIX)} "
                f"handle 0x000a: {len(payload)} bytes"
            )
            if args.delay_ms:
                await asyncio.sleep(args.delay_ms / 1000)

        raster_characteristic = characteristics.get(AE03)
        if raster_characteristic is None:
            raise SystemExit(f"Characteristic not found: {AE03}")
        raster_height = len(rasters[0]) // (WIDTH // 8)
        page_start = build_page_start(raster_height)

        for page_number, page_raster in enumerate(rasters, start=1):
            print(
                f"  page {page_number}/{len(rasters)} "
                f"{len(page_raster)} raster bytes"
            )
            await send_packet(0x000A, page_start)
            if args.delay_ms:
                await asyncio.sleep(args.delay_ms / 1000)
            for offset in range(0, len(page_raster), 480):
                chunk = page_raster[offset:offset + 480]
                properties = {
                    item.lower()
                    for item in raster_characteristic.properties
                }
                await client.write_gatt_char(
                    raster_characteristic.uuid,
                    chunk,
                    response="write" in properties,
                )
                if args.delay_ms:
                    await asyncio.sleep(args.delay_ms / 1000)

        await send_packet(0x000A, CONTROL_SUFFIX)
        print(f"  suffix packet 1/1 handle 0x000a: {len(CONTROL_SUFFIX)} bytes")

        await asyncio.sleep(2)
        for uuid in subscribed:
            await client.stop_notify(uuid)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, BrokenPipeError):
        raise SystemExit(130)
