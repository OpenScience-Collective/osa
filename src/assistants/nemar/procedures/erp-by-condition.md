# ERP by condition

The average event-related potential (ERP) of one channel, one line per condition: the recording read once, re-referenced and low-pass filtered, cut into epochs around each condition's events, and averaged per condition.

## Plan

Propose exactly these five steps, in this order, with these titles.
Fill in only the dataset, recording, channel, conditions and epoch window the reader named.
Each step is one `execute_code` run, and the runs share one Python namespace, so every step uses the variables the earlier ones made.
`est_peak_mb` is the largest step: about three times the size of the read (every EEG channel, 8 bytes a sample), plus about 100 MB for the interpreter.

1. **Read the recording**: one run that reads every EEG channel of the group once, with the read snippet in your instructions, leaves out the labels that contain `EOG`, `ECG`, `EKG` or `EMG`, and prints how many EEG channels remain and which labels it left out.
   Memory: it holds the whole read, channels by samples at 8 bytes a sample.
2. **Filter and clean the recording**: one run that re-references the read to the average of the EEG channels and low-passes it at 30 Hz, zero-phase, on the continuous data.
   Memory: the filtered copy exists beside the read for the run, so about twice the read.
3. **Epoch around the events**: one run that cuts the filtered data from 200 ms before each event to 800 ms after, for each condition, using the `sample_index` lists from the `nemar_get_events` calls, subtracts each epoch's pre-event mean, and drops epochs over 100 µV, printing how many it dropped per condition.
   Memory: the epochs of each condition, small beside the read.
4. **Compute the average per condition**: one run that averages the kept epochs of each condition at the channel, in µV, into one waveform per condition.
   Memory: one waveform per condition, a few KB.
5. **Plot the ERPs**: one run that draws the waveforms, one line per condition, on one axis.
   It reads and computes nothing new.
   Memory: the waveforms and the figure, a few MB.

Use the channel the literature uses for the component when the dataset says which it is (PO8 for ERP CORE's N170); otherwise ask.

## Step 1: read the recording

Read the recording once, every channel of the group (leave out `channels`, as "Which channels" in your instructions says), and compute everything from that read.
Keep the EEG channels by label, matched in upper case anywhere in the label against `EOG`, `ECG`, `EKG` and `EMG`, and say which labels you left out and how many EEG channels remain.

## Step 2: filter and clean the recording

Re-reference to the average of the EEG channels (subtract their mean at each sample).
Then low-pass at 30 Hz, zero-phase, on the continuous data before cutting epochs; `x` is one channel or the whole channels-by-samples array:

```python
from scipy import signal
cutoff = min(30.0, rate / 4)  # Hz, kept well under the Nyquist frequency
sos = signal.butter(4, cutoff, btype="low", fs=rate, output="sos")
filtered = signal.sosfiltfilt(sos, x, axis=-1)
```

`sosfiltfilt` runs the filter forward and back, so nothing shifts in time.

## Step 3: epoch around the events

Cut epochs from 200 ms before each event to 800 ms after, subtract each epoch's pre-event mean, and drop epochs over 100 µV, saying how many you dropped (if none pass, show them all and say so).
The events and their conditions come from `nemar_get_events`, one condition per call: copy each condition's `sample_index` values into the code verbatim, in order, and assert each list's length, as "Events" in your instructions says.

## Step 4: compute the average per condition

Average the kept epochs of each condition at the channel, sample by sample, and keep each condition's kept-epoch count beside its average.

## Step 5: plot the ERPs

Draw one line per condition, time across in milliseconds (-200 to 800), amplitude up in µV, a vertical line at 0 ms, and a legend that names each condition with its kept-epoch count.
The first figure is often all a reader looks at: set the ranges, label both axes with units, and name the recording, the channel and the dropped-epoch counts in the title.
Describe the deflections the averages show, with their latency and size; name a component only when the dataset or its paper says the task evokes it.
Call a recording noisy only when the dropped-epoch count supports it, and if nothing stands out, say so plainly.
