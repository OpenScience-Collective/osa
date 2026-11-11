# Power spectrum

The power spectrum of one recording: Welch's method over a long read, plotted on a log axis.

## Plan

Propose exactly these two steps, in this order, with these titles.
Fill in only the dataset, recording and channel group, and anything the reader named for the plot (a band of frequencies, a channel).
Each step is one `execute_code` run, and the runs share one Python namespace, so step 2 uses `freqs` and `power` from step 1.

1. **Compute the power spectrum**: one run that reads the recording in pieces, gives each channel of each piece its own `signal.welch` call, and averages the estimates over all the pieces, weighted by their segment counts, into `freqs` and `power` (channels by frequencies).
   It prints how many segments and seconds it covered, how many EEG channels it averaged, and which labels it left out.
   There is no separate read step, because the read is part of this run.
   Memory: the run reads one piece at a time, a piece is sized from the reader's budget, and no read passes it however long the recording is, so `est_peak_mb` is at most the reader's budget.
2. **Plot the power spectrum**: one run that draws `power` from step 1 on a log y-axis.
   It reads nothing and computes no spectrum.
   Memory: only `freqs`, `power` and the figure, a few MB.

## Step 1: compute the power spectrum

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

## Step 2: plot the power spectrum

Plot power on a log y-axis over the band that matters (1 to 40 Hz for most EEG).
Before drawing, set the y-limits from the median spectrum across channels in that band: a third of its lowest value at the bottom, three times its highest at the top, raised to include any peak you name.
That keeps a flat or noisy channel from squashing the rest; say how many channels fall outside, if any.
Name any peak you describe with its channel and frequency.
