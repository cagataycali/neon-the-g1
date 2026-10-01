# motions/

Kimodo text-to-motion clips for the G1, one MuJoCo qpos CSV per clip (T x 36:
7 root columns, then 29 joint targets in radians). Generated off-box by
NVIDIA Kimodo (nv-tlabs/kimodo, model Kimodo-G1-RP-v1) and played here by
`tools/kimodo.py` (`kimodo(action="list" | "preview" | "play")`).

Playing a clip streams raw `rt/lowcmd` joint targets and bypasses the onboard
balance controller: `play` is a dry run unless `confirm=True`, and full-body
clips also need `on_gantry=True`. Read the safety block at the top of
`tools/kimodo.py` before adding or playing anything.

| file | rows | note |
| --- | --- | --- |
| kimodo_gantry_test.csv | 120 | first Kimodo clip, gantry only |
| kimodo_right_wave.csv | 120 | right-arm wave from Kimodo |
| right_hand_up.csv | 120 | hand-written right arm raise (legs zero) |
| test_wave.csv | 60 | hand-written short wave (legs zero) |
