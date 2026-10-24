# Merge verification

Person 2 source: mobeenhameed/ForeSmoke, reasoning branch, retrieved through authenticated GitHub API. Source tree SHA: ecb9b114da847b702f9f2ac46b65810b5b1bec95. All 53 source files retrieved; Git history was not cloned because shell Git authentication is unavailable. No remote changes were pushed.

Person 1 source: original ForeSmoke_Person1.zip supplied in this conversation.

94 automated tests passed, including the combined perception-to-reasoning lifecycle, integrated file output, inference failure mapping, and independent live-stall watchdog snapshot logging. Camera/model behavior in those integration tests is mocked. The original 86 reasoning tests remain included.

Synthetic replay: confirmation at 5000 ms; resolution at 9200 ms; same incident ID.
Real observation replay: 133 records processed, zero events using the uncalibrated example polygon. This is not a finding about the physical exit.

Pending: real Mac/iPhone capture and GUI check with this merged version, real model accuracy/cadence after separating raw and tracked model instances, actual zone calibration, and complete live obstruction/removal/disconnection rehearsal. Model weights and a full perception dependency lock are not bundled.

## Synchronized video update

The integrated runner can now save clean camera footage and a horizontal three-panel MP4 with `--record-video`. Detection and final-result panel files are optional with `--record-panel-videos`. The comparison frames come from the exact processed observation and stay aligned with `observations.jsonl`, `states.jsonl`, and lifecycle events. The clean stream is sampled on its own constant timeline before overlays; it is not iPhone sensor RAW. MP4 encoding could not be exercised in the Linux build environment because an OpenCV wheel was unavailable, so the first Mac run must verify codec creation and playback.
