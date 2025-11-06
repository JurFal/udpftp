#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import csv
import re
import matplotlib.pyplot as plt

CSV_NAME = 'test9-10.csv'
HERE = os.path.dirname(__file__)
CSV_PATH = os.path.join(HERE, CSV_NAME)

ROW_PAIRS = [(4, 8), (1, 5), (2, 6), (3, 7)]


def parse_float(text: str):
    if text is None:
        return None
    # extract number (handles "5773.18B/s", "0.4814," etc.)
    m = re.findall(r"[0-9]+\.?[0-9]*", str(text))
    if not m:
        return None
    try:
        return float(m[0])
    except Exception:
        return None


def find_col(header, key_prefix):
    # find first column whose name starts with the given prefix (case-insensitive)
    key_prefix = key_prefix.strip().lower()
    for i, h in enumerate(header):
        if str(h).strip().lower().startswith(key_prefix):
            return i
    return None


def read_csv_rows(csv_path):
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    return header, rows


def build_series(header, rows):
    loss_idx = find_col(header, 'size')
    thr_idx = find_col(header, 'throughput')
    util_idx = find_col(header, 'traffic_util')
    if loss_idx is None or thr_idx is None or util_idx is None:
        raise RuntimeError('Required columns not found in CSV header: loss_rate, throughput, traffic_util')

    labels = []  # loss rates
    thr_gbn, thr_sr = [], []
    util_gbn, util_sr = [], []

    for a, b in ROW_PAIRS:
        # convert to 0-based indices
        ra = rows[a - 1]
        rb = rows[b - 1]
        la = str(ra[loss_idx]).strip()
        lb = str(rb[loss_idx]).strip()
        # prefer the label from the first row; assert equal if possible
        if la != lb:
            # still proceed but note mismatch
            label = f"{la}/{lb}"
        else:
            label = la
        labels.append(label)

        thr_gbn.append(parse_float(ra[thr_idx]))
        thr_sr.append(parse_float(rb[thr_idx]))
        util_gbn.append(parse_float(ra[util_idx]))
        util_sr.append(parse_float(rb[util_idx]))

    return labels, thr_gbn, thr_sr, util_gbn, util_sr


def plot_bars(labels, series_a, series_b, ylabel, title, out_name):
    x = list(range(len(labels)))
    width = 0.35
    plt.figure(figsize=(8, 5))
    plt.bar([i - width / 2 for i in x], series_a, width, label='Reno', color='#1f77b4')
    plt.bar([i + width / 2 for i in x], series_b, width, label='Vegas', color='#ff7f0e')
    plt.xticks(x, labels)
    plt.xlabel('File Size')
    plt.ylabel(ylabel)
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    out_path = os.path.join(HERE, out_name)
    plt.savefig(out_path, dpi=160)
    print(f"Saved: {out_path}")


if __name__ == '__main__':
    header, rows = read_csv_rows(CSV_PATH)
    labels, thr_gbn, thr_sr, util_gbn, util_sr = build_series(header, rows)

    plot_bars(
        labels,
        thr_gbn,
        thr_sr,
        ylabel='Throughput (B/s)',
        title='Reno vs Vegas: Throughput vs File Size (test9-10.csv)',
        out_name='test9-10_throughput.png'
    )

    plot_bars(
        labels,
        util_gbn,
        util_sr,
        ylabel='Traffic Utilization',
        title='Reno vs Vegas: Traffic Utilization vs File Size (test9-10.csv)',
        out_name='test9-10_traffic_util.png'
    )