"""Read per-link bandwidth out of NetFlow, via nfdump.

nfdump is asked for CSV over a recent time window, aggregated by source and
destination address. The header row is read to find the columns rather than
assuming fixed positions, because the column set varies between nfdump builds.

Bytes over the window become kilobits per second, which is what the dashboard
displays.

Matching flows to links by address pair suits router-to-router traffic. If you
would rather chart interface utilisation, aggregate by exporter and input or
output interface instead: change the -A argument and the key built in
_accumulate, and the rest of this module still applies.
"""

import subprocess
import time


class NetflowError(RuntimeError):
    """Any failure reading flows, so callers catch one thing."""


def _window_argument(window_seconds, now=None):
    """nfdump wants "YYYY/MM/DD.hh:mm:ss-YYYY/MM/DD.hh:mm:ss"."""
    end = time.time() if now is None else now
    start = end - window_seconds
    fmt = "%Y/%m/%d.%H:%M:%S"
    return "%s-%s" % (time.strftime(fmt, time.localtime(start)),
                      time.strftime(fmt, time.localtime(end)))


def _accumulate(csv_text):
    """Sum bytes per (source, destination) from nfdump CSV output."""
    totals = {}
    columns = None

    for line in csv_text.splitlines():
        line = line.strip()
        if not line:
            continue

        fields = [field.strip() for field in line.split(",")]

        # The header is the row that names the columns we need.
        if columns is None:
            if "sa" in fields and "da" in fields:
                columns = {name: index for index, name in enumerate(fields)}
                if "ibyt" not in columns:
                    raise NetflowError("nfdump CSV has no ibyt column")
            continue

        # nfdump appends summary lines after the records; they do not parse as
        # a record, so skip anything that does not line up.
        needed = max(columns["sa"], columns["da"], columns["ibyt"])
        if len(fields) <= needed:
            continue

        source = fields[columns["sa"]]
        destination = fields[columns["da"]]
        try:
            octets = float(fields[columns["ibyt"]])
        except ValueError:
            continue

        key = (source, destination)
        totals[key] = totals.get(key, 0.0) + octets

    if columns is None:
        raise NetflowError("nfdump produced no CSV header")
    return totals


def _to_kbps(octets, window_seconds):
    if window_seconds <= 0:
        return 0.0
    return (octets * 8.0) / float(window_seconds) / 1000.0


def read_pair_rates(nfdump_bin, flow_dir, window_seconds, now=None, runner=None):
    """
    (source_ip, destination_ip) -> kbps over the window.

    `runner` exists so the parsing can be exercised without nfdump installed.
    """
    if not flow_dir:
        raise NetflowError("no NETFLOW_DIR configured")

    command = [
        nfdump_bin,
        "-R", flow_dir,
        "-t", _window_argument(window_seconds, now),
        "-o", "csv",
        "-q",
        "-a",
        "-A", "srcip,dstip",
    ]

    run = runner or _run
    try:
        stdout = run(command)
    except OSError as exc:
        raise NetflowError("could not run %s: %s" % (nfdump_bin, exc))

    totals = _accumulate(stdout)
    return {key: _to_kbps(octets, window_seconds) for key, octets in totals.items()}


def _run(command, timeout=15.0):
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if process.returncode != 0:
        raise NetflowError("%s exited %d: %s" % (
            command[0], process.returncode,
            process.stderr.decode("utf-8", "replace").strip()))
    return process.stdout.decode("utf-8", "replace")


def rate_between(rates, source_ip, destination_ip):
    """kbps from one address to another, 0.0 when nothing matched."""
    if not source_ip or not destination_ip:
        return 0.0
    return rates.get((source_ip, destination_ip), 0.0)
