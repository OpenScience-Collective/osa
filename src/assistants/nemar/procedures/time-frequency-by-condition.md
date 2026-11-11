# Time-frequency by condition

The event-related spectral perturbation (ERSP) of one channel, one map per condition: the recording read once, cut into epochs around each condition's events, a Morlet wavelet transform of each epoch, the power averaged per condition, and shown in decibels (dB) relative to the power before the event at each frequency.

## Plan

Propose exactly these four steps, in this order, with these titles.
Fill in only the dataset, recording, channel and conditions the reader named.
Each step is one `execute_code` run, and the runs share one Python namespace, so every step uses the variables the earlier ones made.
`est_peak_mb` is the largest step: about three times the size of the read (every EEG channel, 8 bytes a sample), plus about 100 MB for the interpreter.

This procedure is slow, so warn the reader before they approve it, every time, however short the recording.
The code runs in their browser, in a Python runtime with a single processor and limited compute and memory, so it will likely take a long time, longer with more epochs, more frequencies or a longer window.
Say so in the plan's findings, which the reader sees beside the steps before approving, and again in step 3's detail, and name the ways to make it shorter: fewer frequencies, fewer epochs (one condition first, or a subset of the trials), or a shorter epoch window.
When step 3 runs, say again that it may take a while.

Filtering is NOT a step here, decided once: the wavelet already passes only the frequencies around its own, so a filter would add edge effects and nothing else, and the data are used as recorded.
The data also keep the reference they were recorded with: changing it would take a step titled "Filter and clean the recording", which this plan does not have.

1. **Read the recording**: one run that reads every EEG channel of the group once, with the read snippet in your instructions, leaves out the labels that contain `EOG`, `ECG`, `EKG` or `EMG`, and prints how many EEG channels remain and which labels it left out.
   Memory: it holds the whole read, channels by samples at 8 bytes a sample.
2. **Epoch around the events**: one run that cuts the channel's samples from 0.5 s plus a pad before each event to 1 s plus the pad after it, for each condition, using the `sample_index` lists from the `nemar_get_events` calls, subtracts each epoch's mean, and drops epochs that pass 100 µV from that mean, printing how many it dropped per condition.
   Memory: epochs x samples x 8 bytes for one channel, a few MB.
3. **Compute the time-frequency power**: one run that transforms each epoch with a Morlet wavelet at each frequency from 4 to 40 Hz in steps of 2 Hz, averages the power over the epochs of each condition, and turns it into dB relative to that frequency's mean power from 400 to 100 ms before the event.
   Memory: the array of epochs x frequencies x times at 8 bytes (80 epochs, 19 frequencies and 615 times at 250 Hz is 7.5 MB, and it grows with the rate); the run never holds it, since it averages each frequency's epochs as soon as it has them, so the largest piece is one frequency's epochs x samples at 16 bytes (complex).
4. **Plot the time-frequency maps**: one run that draws one map per condition, time across and frequency up, on one shared symmetric color range with one color bar.
   It reads and computes nothing new.
   Memory: the maps in dB and the figure, a few MB.

Use the channel the literature uses for the component when the dataset says which it is (PO8 for ERP CORE's N170); otherwise ask.

## Step 1: read the recording

Read the recording once, every channel of the group (leave out `channels`, as "Which channels" in your instructions says), and compute everything from that read.
Keep the EEG channels by label, matched in upper case anywhere in the label against `EOG`, `ECG`, `EKG` and `EMG`, and say which labels you left out and how many EEG channels remain.
Keep `labels = window.labels` and `rate = window.rate`; step 2 finds the channel by its label.

## Step 2: epoch around the events

Take the one channel's samples, by its label (`window.data[labels.index("PO8")]`), as they were recorded: no filter and no re-reference.
The recording's offset is large (PO8 in ERP CORE's N170 recording averages about -5,400 µV), which is why each epoch's own mean comes off and why the 100 µV rule is about distance from that mean.

An epoch is wider than the window shown: the lowest frequency's wavelet has a standard deviation of `n_cycles / (2 pi f)` seconds, and its kernel runs three of them to either side, so the first and last three of those standard deviations of an epoch are spoiled by its edges, and are cut off after the transform.
This block makes the pad and the epoch's length once, and the next step uses the same `pre`, `post`, `n_cycles` and `fmin`:

```python
import numpy as np
n_cycles, fmin = 4, 4.0  # cycles in the Morlet wavelet, and the lowest frequency in Hz
pad_s = 3 * n_cycles / (2 * np.pi * fmin)  # s: 3 standard deviations of the lowest frequency's wavelet
pre = int(np.ceil((0.5 + pad_s) * rate))  # samples before the event: 0.5 s plus the pad
post = int(np.ceil((1.0 + pad_s) * rate))  # samples after it: 1.0 s plus the pad
print(f"pad {pad_s:.2f} s; each epoch is {pre} samples before the event and {post} after")
```

Cut `channel[i - pre : i + post]` for each `sample_index` `i` of each condition, skip an epoch that would run off either end of the recording, subtract the epoch's mean, and drop it when any sample is more than 100 µV from zero after that.
Keep the rest as `epochs[condition]`, an array of kept epochs x samples, and say how many you dropped.
The events and their conditions come from `nemar_get_events`, one condition per call: copy each condition's `sample_index` values into the code verbatim, in order, and assert each list's length, as "Events" in your instructions says.

## Step 3: compute the time-frequency power

A Morlet wavelet of 4 cycles at each frequency from 4 Hz to 40 Hz (or a quarter of the rate, if that is lower), in steps of 2 Hz, by `scipy.signal.fftconvolve`, with numpy and SciPy only.
The power is the squared magnitude, averaged over the epochs of each condition, and then each frequency is divided by its mean from 400 to 100 ms before the event and turned into dB: 0 dB is the pre-event level, +3 dB doubles it and -3 dB halves it.
The pad is cut off after the transform, so the maps show -500 to 1,000 ms.
Run this as written, after step 2's block, in the same run as the rest of the step:

```python
import numpy as np
from scipy import signal
freqs = np.arange(fmin, min(40.0, rate / 4) + 1e-9, 2.0)  # Hz
times = (np.arange(pre + post) - pre) / rate  # s from the event, for each sample of an epoch
analysed = (times >= -0.5) & (times <= 1.0)  # the pad on each side is dropped after the transform
base = (times >= -0.4) & (times <= -0.1)  # the pre-event window every frequency is measured against
tf_db, kept = {}, {}  # condition -> frequencies x times, in dB
for condition, trials in epochs.items():  # trials: kept epochs x samples
    assert trials.shape[1] == pre + post, (trials.shape, pre + post)
    mean_power = np.zeros((len(freqs), trials.shape[1]))
    for row, f in enumerate(freqs):  # one frequency at a time, so no epochs x frequencies x times array
        sigma = n_cycles / (2 * np.pi * f)  # s, the standard deviation of the wavelet's envelope
        half = int(np.ceil(3 * sigma * rate))
        tw = np.arange(-half, half + 1) / rate
        wavelet = np.exp(2j * np.pi * f * tw) * np.exp(-(tw**2) / (2 * sigma**2))
        wavelet /= np.sqrt(np.sum(np.abs(wavelet) ** 2))  # unit energy
        analytic = signal.fftconvolve(trials, wavelet[np.newaxis, :], mode="same", axes=-1)
        mean_power[row] = (np.abs(analytic) ** 2).mean(axis=0)  # mean over the epochs
    reference = mean_power[:, base].mean(axis=1, keepdims=True)  # per frequency, over the pre-event window
    tf_db[condition] = 10 * np.log10(mean_power[:, analysed] / reference)
    kept[condition] = trials.shape[0]
    top = np.unravel_index(np.argmax(np.abs(tf_db[condition])), tf_db[condition].shape)
    print(f"{condition}: {kept[condition]} epochs, {len(freqs)} frequencies from {freqs[0]:g} to {freqs[-1]:g} Hz; largest change {tf_db[condition][top]:+.1f} dB at {freqs[top[0]]:g} Hz, {times[analysed][top[1]] * 1000:.0f} ms")
shown = times[analysed]  # s, the time axis of every map
```

It leaves `tf_db` (condition -> frequencies x times, in dB), `freqs`, `shown` (seconds) and `kept` for step 4.
The 4 Hz wavelet is about 0.4 s long to either side of a point, so at low frequencies a change smears over several hundred ms: say so when you describe timing.

## Step 4: plot the time-frequency maps

Draw one panel per condition with `pcolormesh(shown * 1000, freqs, tf_db[condition], shading="auto")`: time across in milliseconds (-500 to 1,000), frequency up in Hz, a vertical line at 0 ms, and a title that names the condition with its kept-epoch count.
Put every panel on one color range, symmetric about 0 (the largest absolute dB over all the conditions), with a diverging color map such as `RdBu_r`, and one color bar labeled "dB relative to -400 to -100 ms".
Name the recording, the channel, the wavelet (Morlet, 4 cycles) and the dropped-epoch counts in the title, and label both axes with units.
The runtime attaches the open figure, so nothing else shows it.
Describe where power rises and falls, in which frequencies and over which latencies, with its size in dB, and how the conditions differ; only from the maps, and do not say what is usually seen in the task.
If nothing stands out, say so plainly.
