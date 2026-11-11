# Band power topography

The power of one recording in one frequency band, drawn as a scalp map: Welch's method over a long read, integrated over the band, and one value per EEG channel interpolated over a head.

## Plan

Propose exactly these two steps, in this order, with these titles.
The spectrum and the band integration are ONE step, decided here once, as in the band power procedure: never propose a step of its own for the integration, and never a separate read step.
Fill in only the dataset, recording, channel group and the band (the reader's band edges, for example `mu`, 8 to 12 Hz); if the reader named no band, ask which one.
Each step is one `execute_code` run, and the runs share one Python namespace, so step 2 uses `band_power`, `bands`, `labels`, `keep` and `window` from step 1.

1. **Compute the band power**: one run that reads the recording in pieces, gives each channel of each piece its own `signal.welch` call, averages the estimates over all the pieces, weighted by their segment counts, into `freqs` and `power`, and then, in the same run, integrates `power` over the reader's band into `band_power` (one value per EEG channel).
   It prints how many segments and seconds it covered, how many EEG channels it averaged, which labels it left out, and the band's mean.
   Memory: the run reads one piece at a time, a piece is sized from the reader's budget, and no read passes it however long the recording is, so `est_peak_mb` is at most the reader's budget.
2. **Plot the scalp map**: one run that gives each EEG channel its position on the head, leaves out the channels that have none and says which, and draws `band_power` of the band as a scalp map with one color bar.
   It reads nothing and computes no spectrum.
   Memory: only `band_power`, the table of positions and the figure, a few MB.

Count the EEG channels that will have a position, from `nemar_list_recordings`, before you propose the plan.
If fewer than 8 will (a recording with only `C3`, `Cz` and `C4`, for one), put a caveat in the plan's `findings`, such as "only 3 channels, so the figure is colored markers on a head, not an interpolated map", and still propose the plan with these two steps.
Never end the turn to ask whether the reader wants to go on.

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

For a scalp map, `bands` holds the reader's one band, for example `bands = {"mu": (8, 12)}`, and nothing else.
The block's four default bands are for a request that names no band, which this procedure does not take.

## Step 2: plot the scalp map

Draw the map of the band's `band_power`, one value per EEG channel, with the labels step 1 kept: `labels` is every label of the group and `keep` the indices of the EEG channels among them, in the order of `band_power`'s columns.

Copy these three blocks as written, into the one run of this step: the table of positions, the matching of labels to it, and the drawing of the figure.
They are the same in every scalp-map procedure.

The positions are the 343 scalp positions of MNE's `standard_1005` montage (the 10-10 and 10-05 systems, with the old names `T3`, `T4`, `T5` and `T6` for `T7`, `T8`, `P7` and `P8`), as `(x, y)` in a head of radius 0.5 with the nose up and the right ear at the right.
They were derived once, at development time, with MNE (which this runtime does not have and this project does not depend on): a sphere was fitted to the montage's 3D positions, each position's angle from the top of that sphere became its distance from the center (an azimuthal equidistant projection, 110 degrees from the top at radius 0.5), the table was shifted so that `Cz` is the origin, and the numbers were rounded to 3 decimals.
The positions below the head's edge (`T9`, `A1` and the like) fall a little outside the circle.

```python
positions = {
    "Fp1": (-0.121, 0.387), "Fpz": (-0.002, 0.392), "Fp2": (0.118, 0.389),
    "AF9": (-0.277, 0.431), "AF7": (-0.237, 0.344), "AF5": (-0.179, 0.329),
    "AF3": (-0.121, 0.311), "AF1": (-0.062, 0.298), "AFz": (-0.001, 0.297),
    "AF2": (0.063, 0.298), "AF4": (0.123, 0.310), "AF6": (0.178, 0.330), "AF8": (0.235, 0.347),
    "AF10": (0.280, 0.432), "F9": (-0.415, 0.321), "F7": (-0.337, 0.261), "F5": (-0.254, 0.233),
    "F3": (-0.169, 0.213), "F1": (-0.084, 0.202), "Fz": (0.000, 0.199), "F2": (0.088, 0.204),
    "F4": (0.172, 0.219), "F6": (0.258, 0.235), "F8": (0.337, 0.264), "F10": (0.417, 0.322),
    "FT9": (-0.506, 0.166), "FT7": (-0.410, 0.135), "FC5": (-0.303, 0.118),
    "FC3": (-0.198, 0.109), "FC1": (-0.097, 0.101), "FCz": (0.000, 0.100),
    "FC2": (0.098, 0.104), "FC4": (0.200, 0.111), "FC6": (0.305, 0.121), "FT8": (0.410, 0.142),
    "FT10": (0.509, 0.169), "T9": (-0.538, -0.016), "T7": (-0.433, -0.017),
    "C5": (-0.315, -0.008), "C3": (-0.205, -0.004), "C1": (-0.099, -0.001),
    "Cz": (0.000, 0.000), "C2": (0.103, 0.000), "C4": (0.208, -0.001), "C6": (0.318, -0.005),
    "T8": (0.436, -0.012), "T10": (0.542, -0.019), "TP9": (-0.496, -0.192),
    "TP7": (-0.401, -0.158), "CP5": (-0.295, -0.130), "CP3": (-0.191, -0.110),
    "CP1": (-0.093, -0.099), "CPz": (0.000, -0.096), "CP2": (0.099, -0.099),
    "CP4": (0.196, -0.108), "CP6": (0.299, -0.126), "TP8": (0.404, -0.158),
    "TP10": (0.498, -0.197), "P9": (-0.404, -0.334), "P7": (-0.324, -0.272),
    "P5": (-0.244, -0.235), "P3": (-0.163, -0.209), "P1": (-0.080, -0.195),
    "Pz": (0.000, -0.190), "P2": (0.085, -0.192), "P4": (0.167, -0.206), "P6": (0.245, -0.236),
    "P8": (0.326, -0.273), "P10": (0.404, -0.339), "PO9": (-0.278, -0.428),
    "PO7": (-0.224, -0.347), "PO5": (-0.177, -0.318), "PO3": (-0.123, -0.299),
    "PO1": (-0.062, -0.290), "POz": (-0.001, -0.286), "PO2": (0.062, -0.291),
    "PO4": (0.122, -0.302), "PO6": (0.178, -0.318), "PO8": (0.224, -0.349),
    "PO10": (0.275, -0.432), "O1": (-0.114, -0.384), "Oz": (-0.002, -0.386),
    "O2": (0.112, -0.385), "I1": (-0.141, -0.475), "Iz": (-0.003, -0.482),
    "I2": (0.137, -0.477), "AFp9h": (-0.205, 0.412), "AFp7h": (-0.158, 0.369),
    "AFp5h": (-0.111, 0.365), "AFp3h": (-0.066, 0.357), "AFp1h": (-0.023, 0.347),
    "AFp2h": (0.023, 0.346), "AFp4h": (0.067, 0.354), "AFp6h": (0.109, 0.366),
    "AFp8h": (0.155, 0.370), "AFp10h": (0.202, 0.414), "AFF9h": (-0.327, 0.341),
    "AFF7h": (-0.256, 0.293), "AFF5h": (-0.185, 0.271), "AFF3h": (-0.113, 0.256),
    "AFF1h": (-0.037, 0.250), "AFF2h": (0.040, 0.249), "AFF4h": (0.115, 0.256),
    "AFF6h": (0.186, 0.274), "AFF8h": (0.256, 0.295), "AFF10h": (0.326, 0.344),
    "FFT9h": (-0.429, 0.221), "FFT7h": (-0.330, 0.191), "FFC5h": (-0.234, 0.169),
    "FFC3h": (-0.138, 0.157), "FFC1h": (-0.044, 0.149), "FFC2h": (0.048, 0.150),
    "FFC4h": (0.140, 0.161), "FFC6h": (0.237, 0.174), "FFT8h": (0.333, 0.193),
    "FFT10h": (0.430, 0.224), "FTT9h": (-0.485, 0.064), "FTT7h": (-0.371, 0.058),
    "FCC5h": (-0.259, 0.053), "FCC3h": (-0.152, 0.051), "FCC1h": (-0.050, 0.050),
    "FCC2h": (0.050, 0.051), "FCC4h": (0.154, 0.054), "FCC6h": (0.262, 0.056),
    "FTT8h": (0.372, 0.063), "FTT10h": (0.488, 0.066), "TTP9h": (-0.476, -0.105),
    "TTP7h": (-0.367, -0.079), "CCP5h": (-0.255, -0.063), "CCP3h": (-0.150, -0.054),
    "CCP1h": (-0.048, -0.049), "CCP2h": (0.051, -0.049), "CCP4h": (0.153, -0.052),
    "CCP6h": (0.258, -0.060), "TTP8h": (0.368, -0.077), "TTP10h": (0.478, -0.105),
    "TPP9h": (-0.411, -0.250), "TPP7h": (-0.318, -0.202), "CPP5h": (-0.224, -0.171),
    "CPP3h": (-0.132, -0.153), "CPP1h": (-0.042, -0.145), "CPP2h": (0.048, -0.143),
    "CPP4h": (0.138, -0.151), "CPP6h": (0.229, -0.169), "TPP8h": (0.321, -0.200),
    "TPP10h": (0.412, -0.253), "PPO9h": (-0.307, -0.355), "PPO7h": (-0.246, -0.295),
    "PPO5h": (-0.181, -0.263), "PPO3h": (-0.110, -0.247), "PPO1h": (-0.036, -0.239),
    "PPO2h": (0.038, -0.236), "PPO4h": (0.112, -0.244), "PPO6h": (0.182, -0.265),
    "PPO8h": (0.246, -0.297), "PPO10h": (0.306, -0.358), "POO9h": (-0.188, -0.415),
    "POO7h": (-0.153, -0.360), "POO5h": (-0.115, -0.344), "POO3h": (-0.070, -0.337),
    "POO1h": (-0.025, -0.335), "POO2h": (0.022, -0.338), "POO4h": (0.069, -0.340),
    "POO6h": (0.113, -0.347), "POO8h": (0.153, -0.361), "POO10h": (0.186, -0.417),
    "OI1h": (-0.063, -0.437), "OI2h": (0.060, -0.438), "Fp1h": (-0.061, 0.395),
    "Fp2h": (0.058, 0.395), "AF9h": (-0.268, 0.381), "AF7h": (-0.209, 0.334),
    "AF5h": (-0.149, 0.321), "AF3h": (-0.093, 0.302), "AF1h": (-0.031, 0.296),
    "AF2h": (0.032, 0.296), "AF4h": (0.094, 0.302), "AF6h": (0.149, 0.321),
    "AF8h": (0.208, 0.337), "AF10h": (0.266, 0.384), "F9h": (-0.383, 0.286),
    "F7h": (-0.296, 0.246), "F5h": (-0.212, 0.222), "F3h": (-0.127, 0.206),
    "F1h": (-0.041, 0.201), "F2h": (0.045, 0.200), "F4h": (0.130, 0.210), "F6h": (0.215, 0.228),
    "F8h": (0.299, 0.248), "F10h": (0.383, 0.290), "FT9h": (-0.464, 0.146),
    "FT7h": (-0.356, 0.126), "FC5h": (-0.251, 0.111), "FC3h": (-0.147, 0.104),
    "FC1h": (-0.048, 0.099), "FC2h": (0.049, 0.101), "FC4h": (0.149, 0.107),
    "FC6h": (0.253, 0.115), "FT8h": (0.357, 0.131), "FT10h": (0.466, 0.149),
    "T9h": (-0.489, -0.023), "T7h": (-0.373, -0.012), "C5h": (-0.259, -0.006),
    "C3h": (-0.151, -0.002), "C1h": (-0.049, 0.000), "C2h": (0.051, 0.000),
    "C4h": (0.155, 0.000), "C6h": (0.263, -0.003), "T8h": (0.376, -0.008),
    "T10h": (0.492, -0.023), "TP9h": (-0.449, -0.181), "TP7h": (-0.348, -0.144),
    "CP5h": (-0.242, -0.118), "CP3h": (-0.142, -0.103), "CP1h": (-0.045, -0.097),
    "CP2h": (0.051, -0.096), "CP4h": (0.147, -0.102), "CP6h": (0.247, -0.116),
    "TP8h": (0.351, -0.141), "TP10h": (0.451, -0.183), "P9h": (-0.362, -0.309),
    "P7h": (-0.284, -0.253), "P5h": (-0.204, -0.219), "P3h": (-0.121, -0.201),
    "P1h": (-0.038, -0.191), "P2h": (0.045, -0.189), "P4h": (0.126, -0.198),
    "P6h": (0.207, -0.218), "P8h": (0.287, -0.252), "P10h": (0.362, -0.311),
    "PO9h": (-0.249, -0.390), "PO7h": (-0.201, -0.332), "PO5h": (-0.151, -0.307),
    "PO3h": (-0.092, -0.294), "PO1h": (-0.031, -0.287), "PO2h": (0.031, -0.289),
    "PO4h": (0.092, -0.296), "PO6h": (0.151, -0.308), "PO8h": (0.201, -0.334),
    "PO10h": (0.247, -0.392), "O1h": (-0.058, -0.389), "O2h": (0.055, -0.390),
    "I1h": (-0.072, -0.483), "I2h": (0.067, -0.484), "AFp9": (-0.200, 0.464),
    "AFp7": (-0.181, 0.371), "AFp5": (-0.134, 0.368), "AFp3": (-0.087, 0.362),
    "AFp1": (-0.046, 0.350), "AFpz": (-0.001, 0.345), "AFp2": (0.046, 0.347),
    "AFp4": (0.088, 0.359), "AFp6": (0.132, 0.369), "AFp8": (0.178, 0.373),
    "AFp10": (0.203, 0.465), "AFF9": (-0.350, 0.384), "AFF7": (-0.288, 0.308),
    "AFF5": (-0.219, 0.284), "AFF3": (-0.149, 0.263), "AFF1": (-0.075, 0.251),
    "AFFz": (-0.001, 0.249), "AFF2": (0.078, 0.251), "AFF4": (0.151, 0.264),
    "AFF6": (0.221, 0.285), "AFF8": (0.287, 0.311), "AFF10": (0.353, 0.385),
    "FFT9": (-0.467, 0.248), "FFT7": (-0.379, 0.202), "FFC5": (-0.282, 0.178),
    "FFC3": (-0.186, 0.163), "FFC1": (-0.091, 0.153), "FFCz": (0.000, 0.150),
    "FFC2": (0.094, 0.155), "FFC4": (0.188, 0.167), "FFC6": (0.285, 0.182),
    "FFT8": (0.379, 0.207), "FFT10": (0.469, 0.249), "FTT9": (-0.529, 0.076),
    "FTT7": (-0.429, 0.061), "FCC5": (-0.314, 0.055), "FCC3": (-0.205, 0.052),
    "FCC1": (-0.101, 0.050), "FCCz": (0.000, 0.050), "FCC2": (0.101, 0.052),
    "FCC4": (0.207, 0.055), "FCC6": (0.315, 0.059), "FTT8": (0.430, 0.067),
    "FTT10": (0.535, 0.078), "TTP9": (-0.525, -0.108), "TTP7": (-0.423, -0.091),
    "CCP5": (-0.310, -0.071), "CCP3": (-0.202, -0.058), "CCP1": (-0.099, -0.051),
    "CCPz": (0.000, -0.048), "CCP2": (0.102, -0.050), "CCP4": (0.205, -0.055),
    "CCP6": (0.312, -0.067), "TTP8": (0.426, -0.088), "TTP10": (0.528, -0.113),
    "TPP9": (-0.456, -0.267), "TPP7": (-0.367, -0.220), "CPP5": (-0.271, -0.185),
    "CPP3": (-0.178, -0.160), "CPP1": (-0.087, -0.147), "CPPz": (0.000, -0.143),
    "CPP2": (0.093, -0.146), "CPP4": (0.183, -0.158), "CPP6": (0.275, -0.182),
    "TPP8": (0.369, -0.220), "TPP10": (0.457, -0.272), "PPO9": (-0.343, -0.388),
    "PPO7": (-0.276, -0.315), "PPO5": (-0.213, -0.279), "PPO3": (-0.147, -0.253),
    "PPO1": (-0.074, -0.241), "PPOz": (0.000, -0.237), "PPO2": (0.075, -0.239),
    "PPO4": (0.147, -0.253), "PPO6": (0.216, -0.277), "PPO8": (0.276, -0.316),
    "PPO10": (0.342, -0.392), "POO9": (-0.210, -0.457), "POO7": (-0.170, -0.370),
    "POO5": (-0.134, -0.352), "POO3": (-0.093, -0.341), "POO1": (-0.048, -0.333),
    "POOz": (-0.001, -0.336), "POO2": (0.045, -0.338), "POO4": (0.092, -0.342),
    "POO6": (0.135, -0.351), "POO8": (0.169, -0.372), "POO10": (0.206, -0.460),
    "OI1": (-0.126, -0.431), "OIz": (-0.002, -0.434), "OI2": (0.123, -0.432),
    "T3": (-0.433, -0.017), "T5": (-0.324, -0.272), "T4": (0.436, -0.012),
    "T6": (0.326, -0.273), "M1": (-0.543, -0.198), "M2": (0.547, -0.203),
    "A1": (-0.575, -0.076), "A2": (0.580, -0.078),
}
```

Matching: a label is matched in upper case, after removing a leading `EEG ` and a trailing `-REF`, `-LE` or `-AVG`, so `EEG Fp1-REF` is `FP1`.
A label with no position is printed and left out, and so is a label whose position an earlier channel already holds (`T3` and `T7` are one place).

```python
import numpy as np
by_name = {name.upper(): xy for name, xy in positions.items()}  # matched in upper case

def locate(labels):
    """Indices of the labels that have a position, and those positions, one row each."""
    found, points, unmatched, repeated = [], [], [], []
    for i, label in enumerate(labels):
        name = label.strip().upper()
        if name.startswith("EEG "):
            name = name[4:]
        for tail in ("-REF", "-LE", "-AVG"):
            if name.endswith(tail):
                name = name[: -len(tail)]
                break
        xy = by_name.get(name.strip())
        if xy is None:
            unmatched.append(label)
        elif xy in points:  # the same spot as an earlier channel (T3 and T7): keep the first
            repeated.append(label)
        else:
            found.append(i)
            points.append(xy)
    print(f"{len(found)} of {len(labels)} channels have a position; left out, no position: {unmatched or 'none'}; left out, same position as an earlier channel: {repeated or 'none'}")
    return np.array(found, dtype=int), np.array(points, dtype=float).reshape(-1, 2)
```

Drawing: one figure with a head for each panel (a head outline with a nose and two ears), the values interpolated with `scipy.interpolate.RBFInterpolator` only inside the head, a marker at each channel, one color bar with the unit, and a title.
With fewer than 8 positioned channels there is nothing to interpolate, so each head shows only its colored markers, each named.

```python
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from matplotlib.patches import Circle, Polygon
from scipy.interpolate import RBFInterpolator

def scalp_maps(panels, xy, names, unit, title, vmin, vmax, cmap="viridis"):
    """One figure: a head for each (subtitle, values) in panels, one shared colorbar.

    xy is the channels' positions from locate(), names their labels, and values has one
    number per row of xy. With fewer than 8 positions there is nothing to interpolate, so
    each head shows only its colored markers, each named.
    """
    xy = np.asarray(xy, dtype=float)
    fig, axes = plt.subplots(1, len(panels), figsize=(3.4 * len(panels) + 1.4, 4.2),
                             squeeze=False, layout="constrained")
    edge = np.linspace(-0.5, 0.5, 161)
    gx, gy = np.meshgrid(edge, edge)
    inside = gx**2 + gy**2 <= 0.5**2  # the head, radius 0.5: the map is drawn only here
    ear = np.linspace(0, np.pi, 30)
    for ax, (subtitle, values) in zip(axes[0], panels):
        values = np.asarray(values, dtype=float)
        if len(xy) >= 8:
            fit = RBFInterpolator(xy, values, kernel="thin_plate_spline", smoothing=1e-9)
            surface = np.full(gx.shape, np.nan)
            surface[inside] = fit(np.column_stack([gx[inside], gy[inside]]))
            ax.imshow(surface, origin="lower", extent=(-0.5, 0.5, -0.5, 0.5),
                      vmin=vmin, vmax=vmax, cmap=cmap)
        ax.scatter(xy[:, 0], xy[:, 1], c=values, vmin=vmin, vmax=vmax, cmap=cmap,
                   s=30 if len(xy) >= 8 else 160, edgecolors="black", linewidths=0.8, zorder=3)
        if len(xy) < 8:
            for (x, y), name in zip(xy, names):
                ax.annotate(name, (x, y), textcoords="offset points", xytext=(0, 12), ha="center", fontsize=9)
        ax.add_patch(Circle((0, 0), 0.5, fill=False, linewidth=1.8, color="black", zorder=4))
        ax.add_patch(Polygon([(-0.09, 0.49), (0, 0.6), (0.09, 0.49)], closed=False, fill=False, linewidth=1.6, edgecolor="black"))
        for side in (-1, 1):  # the ears
            ax.plot(side * (0.5 + 0.045 * np.sin(ear)), 0.1 * np.cos(ear), color="black", linewidth=1.6)
        ax.set_xlim(-0.65, 0.65)
        ax.set_ylim(-0.65, 0.65)
        ax.set_aspect("equal")
        ax.set_axis_off()
        ax.set_title(subtitle)
    colors = ScalarMappable(norm=Normalize(vmin=vmin, vmax=vmax), cmap=cmap)
    fig.colorbar(colors, ax=list(axes[0]), label=unit, shrink=0.8)
    fig.suptitle(title)
    return fig
```

Then draw the map of the reader's band.
Replace `RECORDING` with the dataset and the recording's name, and `band` with the band's key in `bands`:

```python
band = "alpha"  # the reader's band, a key of bands
low, high = bands[band]
names = [labels[i] for i in keep]  # the EEG channels, in the order of band_power's columns
at, xy = locate(names)  # the channels that have a position, and where
values = band_power[band][at]
unit = window.unit.replace("uV", "µV") + "² (absolute power)"
fig = scalp_maps(
    [(f"{band} {low} to {high} Hz", values)], xy, [names[i] for i in at], unit,
    f"RECORDING, {band} {low} to {high} Hz power, {len(at)} EEG channels", 0, values.max(),
)
```

The runtime attaches the open figure, so nothing else shows it.
The color range runs from 0, since power is not negative, to the largest channel, and the title names the recording, the band and how many channels have a position.
Say which channels were left out and why, and which band edges you used.
Describe where the power is largest and smallest and by how much, only from the values, naming the channels; do not say where alpha is usually largest.
When a few channels hold most of the power (a frontal channel next to the eyes, for one), say so, since one channel then sets the whole color range.
If fewer than 8 channels have a position, say that the figure shows markers only and why.
