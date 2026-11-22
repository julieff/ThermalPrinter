# MXW01 Thermal Printer

Standalone Python tools for the Winner Group C15 WG (code 13019), Bluetooth
name `MXW01`. The XAPK in this repository was used to investigate its BLE
protocol. The text printer sends raster images; it does not rely on the
Android app or on a captured CSV file.

## Print text

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Connect/pair the printer if Windows requires it, then print:

```powershell
python .\text_print.py "Hello from Python"
```

Text wraps to the printer's 384-pixel width. The default line height is
24 pixels. Useful options:

```text
--font PATH          TrueType font file
--font-size N        Font size (default 24)
--line-height N      Line spacing in pixels (default 24)
--margin N           Side margin in pixels (default 8)
--name NAME          Bluetooth name to discover (default MXW01)
--address ADDRESS    Connect to a specific Bluetooth address
--delay-ms N         Delay between BLE writes (default 100)
--preview FILE       Save a PBM image and exit
--dry-run            Render without connecting to Bluetooth
--raster-file FILE   Save the raster as hexadecimal chunks
```

For example:

```powershell
python .\text_print.py "A longer passage..." --line-height 24 --dry-run --preview .\preview.pbm
```

## Protocol notes

- The printer exposes BLE GATT services. The print utility writes control
  packets to characteristic `0000ae01-0000-1000-8000-00805f9b34fb` and
  raster data to `0000ae03-0000-1000-8000-00805f9b34fb`.
- `0000ae02-0000-1000-8000-00805f9b34fb` provides printer notifications.
- Control packets use the vendor prefix `22 21`. Printing starts with
  captured initialization commands and an `A9` raster-start command;
  the raster is sent in 480-byte chunks and completed with an `AD` command.
- Raster rows are 384 pixels wide (48 bytes). The start command's height
  field is set to the generated raster height, so long text can be sent as
  one job. The control bytes are built into `text_print.py`.

The protocol details above are based on the bundled app and successful
communication with an `MXW01`; other firmware variants may differ.

## Diagnostic utilities

`ble_raw.py` discovers GATT characteristics and sends raw hexadecimal
payloads. For example:

```powershell
python .\ble_raw.py --hex 5178A30001000000FF
```

`compare_capture.py` can split raster jobs from a Wireshark ATT export.
The `captured-jobs` directory contains sample raster captures used for
comparison. These tools are optional; `text_print.py` does not use them
or require `mxw_print_sequence.csv`.
