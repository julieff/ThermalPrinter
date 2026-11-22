import argparse
import asyncio
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("Install Bleak first: python -m pip install bleak", file=sys.stderr)
    raise SystemExit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Discover a BLE printer GATT interface and send raw hex."
    )
    parser.add_argument("--name", default="MXW01", help="Bluetooth advertised name")
    parser.add_argument("--address", help="Bluetooth address or UUID")
    payload_group = parser.add_mutually_exclusive_group(required=True)
    payload_group.add_argument("--hex", help="Hex payload, e.g. 5178A30001000000FF")
    payload_group.add_argument(
        "--hex-file",
        help="Text file containing one complete hex payload per line",
    )
    payload_group.add_argument(
        "--sequence-csv",
        help="CSV containing ATT handle and hex payload columns",
    )
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=10,
        help="Delay between payload lines read from --hex-file",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--characteristic",
        help="Writable characteristic UUID to use instead of automatic selection",
    )
    parser.add_argument(
        "--listen-seconds",
        type=float,
        default=3.0,
        help="Seconds to listen for notifications after writing",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    if args.chunk_size < 1:
        raise SystemExit("--chunk-size must be positive")
    if args.delay_ms < 0:
        raise SystemExit("--delay-ms must not be negative")

    payload_lines = [args.hex] if args.hex else []
    if args.hex_file:
        try:
            with open(args.hex_file, "r", encoding="ascii") as payload_file:
                payload_lines = [
                    line.strip()
                    for line in payload_file
                    if line.strip() and not line.strip().startswith("#")
                ]
        except OSError as exc:
            raise SystemExit(f"Could not read --hex-file: {exc}") from exc

    payloads = []
    sequence_rows = []
    if args.sequence_csv:
        try:
            with open(args.sequence_csv, "r", encoding="ascii") as sequence_file:
                for index, line in enumerate(sequence_file, start=1):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    separator = "\t" if "\t" in line else ","
                    fields = [field.strip().strip('"') for field in line.split(separator)]
                    if len(fields) < 2:
                        raise SystemExit(
                            f"Sequence line {index} must contain handle,hex"
                        )
                    handle = fields[-2].lower()
                    hex_payload = fields[-1]
                    try:
                        if handle.startswith("0x"):
                            handle_number = int(handle, 16)
                        elif handle.isdecimal():
                            handle_number = int(handle, 10)
                        else:
                            handle_number = int(handle, 16)
                    except ValueError as exc:
                        raise SystemExit(
                            f"Invalid ATT handle on sequence line {index}"
                        ) from exc
                    hex_payload = (
                        "".join(hex_payload.split())
                        .replace("0x", "")
                        .replace(":", "")
                        .replace("-", "")
                    )
                    if len(hex_payload) % 2:
                        raise SystemExit(
                            f"Hex payload line {index} must contain complete bytes"
                        )
                    try:
                        payload = bytes.fromhex(hex_payload)
                    except ValueError as exc:
                        raise SystemExit(
                            f"Invalid hexadecimal payload on line {index}: {exc}"
                        ) from exc
                    sequence_rows.append((handle_number, payload))
        except OSError as exc:
            raise SystemExit(f"Could not read --sequence-csv: {exc}") from exc
    else:
        for index, hex_payload in enumerate(payload_lines, start=1):
            hex_payload = (
                "".join(hex_payload.split())
                .replace("0x", "")
                .replace(":", "")
                .replace("-", "")
            )
            if len(hex_payload) % 2:
                raise SystemExit(f"Hex payload line {index} must contain complete bytes")
            try:
                payloads.append(bytes.fromhex(hex_payload))
            except ValueError as exc:
                raise SystemExit(
                    f"Invalid hexadecimal payload on line {index}: {exc}"
                ) from exc

    if not payloads and not sequence_rows:
        raise SystemExit("No payloads were supplied")

    device = None
    if args.address:
        device = await BleakScanner.find_device_by_address(
            args.address, timeout=args.timeout
        )
    else:
        devices = await BleakScanner.discover(timeout=args.timeout)
        device = next(
            (item for item in devices if item.name and args.name.lower() in item.name.lower()),
            None,
        )

    if device is None:
        raise SystemExit(f"Printer not found: {args.address or args.name}")

    async with BleakClient(device) as client:
        print(f"Connected: {device.name or 'unknown'} ({device.address})")
        candidates = []
        named_characteristics = {}
        listening_characteristics = []

        for service in client.services:
            print(f"Service: {service.uuid}")
            for characteristic in service.characteristics:
                properties = ", ".join(characteristic.properties)
                print(f"  Characteristic: {characteristic.uuid} [{properties}]")
                if "write" in characteristic.properties or "write-without-response" in characteristic.properties:
                    candidates.append(characteristic)
                    named_characteristics[characteristic.uuid.lower()] = characteristic
                if "notify" in characteristic.properties or "indicate" in characteristic.properties:
                    listening_characteristics.append(characteristic)

        if not candidates:
            raise SystemExit("No writable GATT characteristic found")

        if args.characteristic:
            requested = args.characteristic.lower()
            characteristic = named_characteristics.get(requested)
            if characteristic is None:
                raise SystemExit(
                    f"Characteristic {args.characteristic} is not writable "
                    "or was not discovered"
                )
        else:
            printer_preferred = (
                "0000ae01-0000-1000-8000-00805f9b34fb",
                "0000ae03-0000-1000-8000-00805f9b34fb",
                "0000ae3b-0000-1000-8000-00805f9b34fb",
            )
            characteristic = next(
                (
                    named_characteristics[uuid]
                    for uuid in printer_preferred
                    if uuid in named_characteristics
                ),
                None,
            )
            if characteristic is None:
                printer_candidates = [
                    item
                    for item in candidates
                    if item.service_uuid.lower().startswith("0000ae")
                    and "read" not in item.properties
                ]
                characteristic = printer_candidates[0] if printer_candidates else candidates[0]

        def notification_handler(sender, data):
            hex_data = data.hex().upper()
            print(f"Notification from {sender}: {hex_data}")

        subscribed = []
        preferred_notifications = (
            "0000ae02-0000-1000-8000-00805f9b34fb",
            "0000ae04-0000-1000-8000-00805f9b34fb",
            "0000ae05-0000-1000-8000-00805f9b34fb",
            "0000ae3c-0000-1000-8000-00805f9b34fb",
        )
        notifications_to_subscribe = [
            item
            for item in listening_characteristics
            if item.uuid.lower() in preferred_notifications
        ]

        for notification_characteristic in notifications_to_subscribe:
            await client.start_notify(
                notification_characteristic.uuid,
                notification_handler,
            )
            subscribed.append(notification_characteristic)
            print(f"Listening: {notification_characteristic.uuid}")

        if sequence_rows:
            handle_to_uuid = {
                0x000A: "0000ae01-0000-1000-8000-00805f9b34fb",
                0x000F: "0000ae03-0000-1000-8000-00805f9b34fb",
            }
            print(f"Writing {len(sequence_rows)} captured packet(s)")
            for payload_index, (handle, payload) in enumerate(sequence_rows, start=1):
                uuid = handle_to_uuid.get(handle)
                if uuid is None:
                    raise SystemExit(
                        f"Unhandled ATT handle 0x{handle:04x} "
                        "on sequence line"
                    )
                target = named_characteristics.get(uuid)
                if target is None:
                    raise SystemExit(
                        f"Printer characteristic {uuid} was not discovered"
                    )
                response = "write" in target.properties
                await client.write_gatt_char(
                    target.uuid,
                    payload,
                    response=response,
                )
                print(
                    f"  packet {payload_index}/{len(sequence_rows)} "
                    f"handle 0x{handle:04x} -> {target.uuid}: "
                    f"{len(payload)} bytes"
                )
                if payload_index < len(sequence_rows) and args.delay_ms:
                    await asyncio.sleep(args.delay_ms / 1000)
        else:
            print(f"Writing {len(payloads)} payload(s) to {characteristic.uuid}")
            response = "write" in characteristic.properties
            for payload_index, payload in enumerate(payloads, start=1):
                print(
                    f"Payload {payload_index}/{len(payloads)}: "
                    f"{len(payload)} bytes"
                )
                chunks = [payload]
                if not args.hex_file:
                    chunks = [
                        payload[offset:offset + args.chunk_size]
                        for offset in range(0, len(payload), args.chunk_size)
                    ]
                for chunk_index, chunk in enumerate(chunks, start=1):
                    await client.write_gatt_char(
                        characteristic.uuid,
                        chunk,
                        response=response,
                    )
                    print(
                        f"  wrote chunk {chunk_index}/{len(chunks)} "
                        f"({len(chunk)} bytes)"
                    )
                if payload_index < len(payloads) and args.delay_ms:
                    await asyncio.sleep(args.delay_ms / 1000)

        if subscribed and args.listen_seconds > 0:
            print(f"Listening for notifications for {args.listen_seconds:g} seconds...")
            await asyncio.sleep(args.listen_seconds)

        for notification_characteristic in subscribed:
            await client.stop_notify(notification_characteristic.uuid)


if __name__ == "__main__":
    asyncio.run(main())
