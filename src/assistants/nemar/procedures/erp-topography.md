# ERP topography

The scalp map of the average event-related potential (ERP) in a time window, one map per condition and their difference when there are two: the recording read once, re-referenced and low-pass filtered, cut into epochs around each condition's events with every EEG channel kept, averaged per condition, and the average over the window drawn over a head.

## Plan

Propose exactly these five steps, in this order, with these titles.
Fill in only the dataset, recording, conditions and the time window the reader named (for example 150 to 190 ms); if the reader named a component but no window, ask for the window.
Each step is one `execute_code` run, and the runs share one Python namespace, so every step uses the variables the earlier ones made.
`est_peak_mb` is the largest step: about three times the size of the read (every EEG channel, 8 bytes a sample), plus about 100 MB for the interpreter, or the epochs of step 3 if they are larger.

1. **Read the recording**: one run that reads every EEG channel of the group once, with the read snippet in your instructions, leaves out the labels that contain `EOG`, `ECG`, `EKG` or `EMG`, and prints how many EEG channels remain and which labels it left out.
   Memory: it holds the whole read, channels by samples at 8 bytes a sample.
2. **Filter and clean the recording**: one run that re-references the read to the average of the EEG channels and low-passes it at 30 Hz, zero-phase, on the continuous data.
   Memory: the filtered copy exists beside the read for the run, so about twice the read.
3. **Epoch around the events**: one run that cuts the filtered data, every EEG channel, from 200 ms before each event to 800 ms after, for each condition, using the `sample_index` lists from the `nemar_get_events` calls, subtracts each channel's pre-event mean in each epoch, and drops epochs in which any EEG channel passes 100 µV, printing how many it dropped per condition.
   Memory: epochs x channels x samples x 8 bytes for each condition, bigger than for one channel (80 epochs of 30 channels at 250 Hz for 1 s is 4.6 MB, and 400 epochs of 128 channels at 1000 Hz is 410 MB); when the conditions together would pass the reader's budget, hold one condition's epochs at a time and keep only a running sum per condition (see Step 3).
4. **Compute the average per condition**: one run that averages the kept epochs of each condition over the epochs and over the reader's time window, in µV, into `values[condition]`, one value per EEG channel.
   Memory: one value per channel per condition, a few KB.
5. **Plot the scalp maps**: one run that gives each EEG channel its position on the head, leaves out the channels that have none and says which, and draws one map per condition, and their difference when there are two, on one shared symmetric color range with one color bar.
   It reads and computes nothing new.
   Memory: the values, the table of positions and the figure, a few MB.

Count the EEG channels that will have a position, from `nemar_list_recordings`, before you propose the plan.
If fewer than 8 will (a recording with only `C3`, `Cz` and `C4`, for one), put a caveat in the plan's `findings`, such as "only 3 channels, so the figure is colored markers on a head, not an interpolated map", and still propose the plan with these five steps.
Never end the turn to ask whether the reader wants to go on.

## Step 1: read the recording

Read the recording once, every channel of the group (leave out `channels`, as "Which channels" in your instructions says), and compute everything from that read.
Keep the EEG channels by label, matched in upper case anywhere in the label against `EOG`, `ECG`, `EKG` and `EMG`, and say which labels you left out and how many EEG channels remain.
Keep `labels = window.labels` (every label of the group), `keep` (the indices of the EEG channels) and `rate = window.rate`; step 5 needs the labels.

## Step 2: filter and clean the recording

Re-reference to the average of the EEG channels (subtract their mean at each sample); a scalp map is only as good as its reference, and this is the one the maps are drawn against.
Then low-pass at 30 Hz, zero-phase, on the continuous data before cutting epochs; `x` is one channel or the whole channels-by-samples array:

```python
from scipy import signal
cutoff = min(30.0, rate / 4)  # Hz, kept well under the Nyquist frequency
sos = signal.butter(4, cutoff, btype="low", fs=rate, output="sos")
filtered = signal.sosfiltfilt(sos, x, axis=-1)
```

`sosfiltfilt` runs the filter forward and back, so nothing shifts in time.

## Step 3: epoch around the events

Cut epochs from 200 ms before each event to 800 ms after, every EEG channel, subtract each channel's pre-event mean in each epoch, and drop an epoch when any EEG channel passes 100 µV in it, saying how many you dropped (if none pass, show them all and say so).
Frontal channels next to the eyes make most of the drops, so say so when the counts are high.
The events and their conditions come from `nemar_get_events`, one condition per call: copy each condition's `sample_index` values into the code verbatim, in order, and assert each list's length, as "Events" in your instructions says.

The epochs of one condition are epochs x channels x samples at 8 bytes, which is bigger than the single channel of the ERP procedures, and all the conditions together may pass the reader's budget.
When they would, do not hold them: cut one condition at a time, add its kept epochs into a running sum per condition (`sums[condition]`, channels x samples), keep its kept-epoch count in `kept[condition]`, and let that condition's epochs go before the next.
Otherwise keep the epochs of each condition as `epochs[condition]`.
Either way, keep `dropped[condition]`.

## Step 4: compute the average per condition

Average each condition's kept epochs over the epochs (or divide `sums[condition]` by `kept[condition]`), and then over the samples inside the reader's time window, both ends included, giving `values[condition]`: one value per EEG channel, in µV, in the order of `keep`.
Keep each condition's kept-epoch count in `kept[condition]`.
Print, for each condition, the three channels with the most negative and the most positive values.

## Step 5: plot the scalp maps

Copy these three blocks as written, into the one run of this step: the table of positions, the matching of labels to it, and the drawing of the figure.
They are the same in every scalp-map procedure.

The positions are 92 scalp positions of MNE's `standard_1005` montage: the 10-10 system (`Fp1` to `Iz`), the old names `T3`, `T4`, `T5` and `T6` for `T7`, `T8`, `P7` and `P8`, and the reference sites `A1`, `A2`, `M1` and `M2`, as `(x, y)` in a head of radius 0.5 with the nose up and the right ear at the right.
The 10-05 labels in between (`AFp1h`, `FFC1h` and the like) are left out: a recording that uses them has those channels reported as left out, with no position.
They were derived once, at development time, with MNE (which this runtime does not have and this project does not depend on): a sphere was fitted to the montage's 3D positions, each position's angle from the top of that sphere became its distance from the center (an azimuthal equidistant projection, 110 degrees from the top at radius 0.5), the table was shifted so that `Cz` is the origin, and the numbers were rounded to 3 decimals.
The positions below the head's edge (`T9`, `A1` and the like) fall a little outside the circle.

```python
positions = {
    "Fp1": (-0.121, 0.387), "Fpz": (-0.002, 0.392), "Fp2": (0.118, 0.389), "AF9": (-0.277, 0.431), "AF7": (-0.237, 0.344), "AF5": (-0.179, 0.329), "AF3": (-0.121, 0.311), "AF1": (-0.062, 0.298), "AFz": (-0.001, 0.297),
    "AF2": (0.063, 0.298), "AF4": (0.123, 0.310), "AF6": (0.178, 0.330), "AF8": (0.235, 0.347), "AF10": (0.280, 0.432), "F9": (-0.415, 0.321), "F7": (-0.337, 0.261), "F5": (-0.254, 0.233), "F3": (-0.169, 0.213),
    "F1": (-0.084, 0.202), "Fz": (0.000, 0.199), "F2": (0.088, 0.204), "F4": (0.172, 0.219), "F6": (0.258, 0.235), "F8": (0.337, 0.264), "F10": (0.417, 0.322), "FT9": (-0.506, 0.166), "FT7": (-0.410, 0.135),
    "FC5": (-0.303, 0.118), "FC3": (-0.198, 0.109), "FC1": (-0.097, 0.101), "FCz": (0.000, 0.100), "FC2": (0.098, 0.104), "FC4": (0.200, 0.111), "FC6": (0.305, 0.121), "FT8": (0.410, 0.142), "FT10": (0.509, 0.169),
    "T9": (-0.538, -0.016), "T7": (-0.433, -0.017), "C5": (-0.315, -0.008), "C3": (-0.205, -0.004), "C1": (-0.099, -0.001), "Cz": (0.000, 0.000), "C2": (0.103, 0.000), "C4": (0.208, -0.001), "C6": (0.318, -0.005),
    "T8": (0.436, -0.012), "T10": (0.542, -0.019), "TP9": (-0.496, -0.192), "TP7": (-0.401, -0.158), "CP5": (-0.295, -0.130), "CP3": (-0.191, -0.110), "CP1": (-0.093, -0.099), "CPz": (0.000, -0.096), "CP2": (0.099, -0.099),
    "CP4": (0.196, -0.108), "CP6": (0.299, -0.126), "TP8": (0.404, -0.158), "TP10": (0.498, -0.197), "P9": (-0.404, -0.334), "P7": (-0.324, -0.272), "P5": (-0.244, -0.235), "P3": (-0.163, -0.209), "P1": (-0.080, -0.195),
    "Pz": (0.000, -0.190), "P2": (0.085, -0.192), "P4": (0.167, -0.206), "P6": (0.245, -0.236), "P8": (0.326, -0.273), "P10": (0.404, -0.339), "PO9": (-0.278, -0.428), "PO7": (-0.224, -0.347), "PO5": (-0.177, -0.318),
    "PO3": (-0.123, -0.299), "PO1": (-0.062, -0.290), "POz": (-0.001, -0.286), "PO2": (0.062, -0.291), "PO4": (0.122, -0.302), "PO6": (0.178, -0.318), "PO8": (0.224, -0.349), "PO10": (0.275, -0.432), "O1": (-0.114, -0.384),
    "Oz": (-0.002, -0.386), "O2": (0.112, -0.385), "Iz": (-0.003, -0.482), "T3": (-0.433, -0.017), "T5": (-0.324, -0.272), "T4": (0.436, -0.012), "T6": (0.326, -0.273), "M1": (-0.543, -0.198), "M2": (0.547, -0.203),
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

Then draw the maps, one per condition and, when there are exactly two conditions, the first minus the second.
Replace `RECORDING` with the dataset and the recording's name, and `START` and `END` with the window in ms:

```python
names = [labels[i] for i in keep]  # the EEG channels, in the order of values
at, xy = locate(names)  # the channels that have a position, and where
panels = [(f"{condition} (n={kept[condition]})", values[condition][at]) for condition in values]
if len(values) == 2:
    first, second = values
    panels.append((f"{first} minus {second}", (values[first] - values[second])[at]))
limit = max(np.abs(panel[1]).max() for panel in panels)  # one symmetric range for every map
fig = scalp_maps(
    panels, xy, [names[i] for i in at], "µV",
    f"RECORDING, mean over START to END ms, {len(at)} EEG channels, dropped {dropped}",
    -limit, limit, cmap="RdBu_r",
)
```

The runtime attaches the open figure, so nothing else shows it.
Every map shares one color range, symmetric about 0, so that a color means the same on each; the difference is usually the smallest of them, so when it stays under a fifth of the range, say that it is faint on this range and offer to draw it on a range of its own.
Say which channels were left out and why, and which window you averaged.
Describe where each condition is most negative and most positive and the sign and the channels of the difference, only from the values; name a component only when the dataset or its paper says the task evokes it, and do not say where it is usually largest.
If fewer than 8 channels have a position, say that the figure shows markers only and why.
