# ERP image by condition

An ERP image of one channel, one panel per condition: the recording read once, re-referenced and low-pass filtered, cut into epochs around each condition's events, and drawn as one image of the single-trial epochs per condition, with that condition's average beneath it.

## Plan

Propose exactly these four steps, in this order, with these titles.
Fill in only the dataset, recording, channel, conditions and the ordering the reader named.
Each step is one `execute_code` run, and the runs share one Python namespace, so every step uses the variables the earlier ones made.
`est_peak_mb` is the largest step: about three times the size of the read (every EEG channel, 8 bytes a sample), plus about 100 MB for the interpreter.

1. **Read the recording**: one run that reads every EEG channel of the group once, with the read snippet in your instructions, leaves out the labels that contain `EOG`, `ECG`, `EKG` or `EMG`, and prints how many EEG channels remain and which labels it left out.
   Memory: it holds the whole read, channels by samples at 8 bytes a sample.
2. **Filter and clean the recording**: one run that re-references the read to the average of the EEG channels and low-passes it at 30 Hz, zero-phase, on the continuous data.
   Memory: the filtered copy exists beside the read for the run, so about twice the read.
3. **Epoch around the events**: one run that cuts the filtered data from 200 ms before each event to 800 ms after, for each condition, using the `sample_index` lists (and the response times, when the ordering needs them) from the `nemar_get_events` calls, subtracts each epoch's pre-event mean, and drops epochs over 100 µV, printing how many it dropped per condition.
   Memory: the epochs of each condition, small beside the read.
4. **Plot the ERP images**: one run that orders and smooths the rows, sets the shared color range, and draws one panel per condition, each with its average beneath it.
   It reads and filters nothing new.
   Memory: one channel's epochs for each condition and the figure, a few MB.

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
When the ordering in step 4 uses response times, copy them with each `sample_index` as pairs under the same length assert.

## Step 4: plot the ERP images

An ERP image is one channel's epochs as the rows of an `imshow`, time across in milliseconds (`extent` from -200 to 800) and epoch up, with a diverging colormap centered on zero, one panel per condition and that condition's average beneath it.
Order the rows, then average each row with its neighbors (five epochs): rows in recorded order, even smoothed, show little of how the response varies from trial to trial.
Use the reader's ordering if they named one.
Otherwise order by response time when the events have it: a `response_time` column listed in `columns_summary` (in seconds), requested in `columns` beside `event_type` and copied with each `sample_index` as pairs under the same length assert, or else the first response event from 0 to 800 ms after each stimulus, from its own call.
Put rows with no response last, and mark each response on the image.
Without response times, order by each epoch's mean amplitude in a window around the deflection the average shows, the same window in every panel, and say that this ordering is built in and is not a finding.
Drop the same rows from the order key as from the epochs.
Set the color range before drawing: symmetric about zero, at the 95th percentile of the absolute values of every panel's smoothed rows, shared by all panels.
Then say how the rows are ordered and offer one other ordering, such as recorded order to show drift across the session; that offer is one of your follow-up suggestions, and you do not run it unasked.
Describe the deflections the averages show, with their latency and size; name a component only when the dataset or its paper says the task evokes it.
Averaging and ordering by response time bring a response out of the ongoing activity, while an amplitude ordering only builds structure in its own window.
Describe what the image shows, call a recording noisy only when the dropped-epoch count supports it, and if nothing stands out, say so plainly.
