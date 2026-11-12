# Band power

The power of one recording in frequency bands (delta, theta, alpha, beta, or the reader's own): Welch's method over a long read, integrated over each band, plotted as a bar chart.

## Plan

Propose exactly these two steps, in this order, with these titles.
The spectrum and the band integration are ONE step, decided here once: never propose a step of its own for the integration, and never a separate read step.
Fill in only the dataset, recording, channel group and bands (the reader's band edges, or the defaults in the second block).
Each step is one `execute_code` run, and the runs share one Python namespace, so step 2 uses `band_power` and `bands` from step 1.

1. **Compute the band power**: one run that reads the recording in pieces, gives each channel of each piece its own `signal.welch` call, averages the estimates over all the pieces, weighted by their segment counts, into `freqs` and `power`, and then, in the same run, integrates `power` over each band into `band_power` (one value per EEG channel for each band).
   It prints how many segments and seconds it covered, how many EEG channels it averaged, which labels it left out, and each band's mean.
   Memory: the run reads one piece at a time, a piece is sized from the reader's budget, and no read passes it however long the recording is, so `est_peak_mb` is at most the reader's budget.
2. **Plot the band power**: one run that draws `band_power` from step 1 as a bar chart.
   It reads nothing and computes no spectrum.
   Memory: only `band_power`, `bands` and the figure, a few MB.

## Step 1: compute the band power

Welch's method over a long read (a minute or more, or the whole recording), with 2-second segments, ONE CHANNEL AT A TIME.
Read the recording in pieces, give each channel of each piece its own `signal.welch` call, and average the estimates over all the pieces, weighted by their segment counts, which is exactly what one call over the whole channel would return.
Replace `DATASET_ID`, `RECORDING_PATH` and `GROUP` as in the read snippet in your instructions, and `budget_mb` with the reader's budget as your instructions state it; change nothing else:

```python
import numpy as np
from scipy import signal
import eegprep_lean
index = await eegprep_lean.read_index("DATASET_ID")
store = index.store("RECORDING_PATH")
group = store.group("GROUP")
rate = group.rate
nperseg = int(2 * rate)
noverlap = nperseg // 2
hop = nperseg - noverlap  # welch steps by this many samples from segment to segment
budget_mb = 512  # the reader's browser memory budget, as your instructions state it
total = (group.n_samples - noverlap) // hop  # segments one welch call over the whole channel would average
assert total >= 1, "the recording is shorter than one segment"
# Samples in one piece: the most one welch call takes in this runtime, and what a quarter of
# the budget holds of the read (every channel, 8 bytes a sample) and welch's working space
# for one channel (about 90 bytes a sample; this allows 160). A quarter, since the runtime's
# memory comes to about three times the read, plus about 100 MB for the interpreter.
piece = min(2**28 // nperseg, budget_mb * 2**20 // 4 // (group.n_channels * 8 + 160))
m = max(1, (piece - nperseg) // hop + 1)  # segments in a piece
keep, summed, used = None, 0.0, 0
for first in range(0, total, m):  # the piece's first segment; it starts at sample first * hop
    k = min(m, total - first)  # segments in this piece
    n = (k - 1) * hop + nperseg  # the samples of exactly those k segments
    window = await eegprep_lean.read_window(
        index, store, group=group, start_sample=first * hop, n_samples=n,
    )
    assert window.data.shape[1] == n, "the read came back short"
    if keep is None:  # the EEG channels, by label, as the first piece shows them
        labels = window.labels or [""] * window.data.shape[0]
        keep = [i for i, label in enumerate(labels)
                if not any(tag in label.upper() for tag in ("EOG", "ECG", "EKG", "EMG"))]
        left_out = [labels[i] for i in range(len(labels)) if i not in keep]
        summed = np.zeros((len(keep), nperseg // 2 + 1))
    for row, i in enumerate(keep):  # one channel at a time
        freqs, p = signal.welch(window.data[i], fs=rate, nperseg=nperseg, noverlap=noverlap)
        summed[row] += k * p
    used += k
power = summed / used  # channels x frequencies
assert used == total, (used, total)
covered = (total * hop + noverlap) / rate  # seconds those segments span
print(f"{total} segments of 2 s, {covered:.0f} s of the recording, in pieces of {m} segments, {len(keep)} EEG channels, left out: {left_out or 'none'}")
```

`signal.welch` makes a view of every window at once, and this runtime (32-bit WebAssembly) refuses a view of 2 GiB or more, which is samples x nperseg x 8 bytes for one channel and channels times that for an array.
So a channels-by-samples array raises "array is too big" even at 33 channels by 30,000 samples, and so does one channel past `2**28 // nperseg` samples (about 36 minutes at 250 Hz, 2 at 1000 Hz).
Never pass it more than one channel or more than one piece.
A piece is also what one read takes, so no read passes the budget however long the recording is; with many channels the budget, not welch, sets the piece.
The pieces start every `m * hop` samples and each holds exactly its `k` segments, so together they cover every segment one call over the whole channel would: the spectrum is of the whole recording, and the printed line says how many segments and seconds that was and how many EEG channels it averaged.
Say so in your answer.

Then, in the same `execute_code` run, directly after that block, integrate the spectrum over each band.
Use the reader's band edges in `bands` if they gave any; otherwise keep these: delta 1 to 4 Hz, theta 4 to 8 Hz, alpha 8 to 13 Hz, beta 13 to 30 Hz.
Change nothing else:

```python
from scipy.integrate import trapezoid
bands = {"delta": (1, 4), "theta": (4, 8), "alpha": (8, 13), "beta": (13, 30)}  # Hz, the reader's bands if given
band_power = {}  # band -> absolute power of each EEG channel, in the data's unit squared
for name, (low, high) in bands.items():
    inside = (freqs >= low) & (freqs <= high)
    assert inside.sum() >= 2, f"fewer than two frequency bins inside {name} ({low} to {high} Hz)"
    band_power[name] = trapezoid(power[:, inside], freqs[inside], axis=1)
    print(f"{name} {low} to {high} Hz: mean {band_power[name].mean():.4g} over {len(keep)} EEG channels")
```

The integral is the trapezoid rule over the frequency bins from each band's low edge to its high edge, so with 2-second segments (0.5 Hz bins) edges on a multiple of 0.5 Hz are exact, and any other edge uses the bins inside it.
The values are in the data's unit squared (µV² for these recordings), per EEG channel: absolute power, not a share of the total.
Say which band edges you used.

## Step 2: plot the band power

Plot a bar chart with one bar per band, its height the mean of that band's power over the EEG channels, and the spread across channels as error bars (the standard deviation, saying so).
Label the y-axis with the unit and the bands with their edges (for example `alpha 8 to 13 Hz`), and name the recording in the title with the number of EEG channels averaged.
Describe which band holds the most power and by how much, only from the values.
If a band is much larger than the others, a log y-axis shows them all; say that you used it.
