# Unofficial personal sunnypilot fork

This repository is an unofficial, noncommercial personal fork maintained for a 2018 Subaru
Outback. It is not affiliated with, sponsored by, supported by, or endorsed by comma.ai or
SUNNYPILOT LLC. It is Level 2 driver-assistance software: the driver must remain attentive and
ready to take control immediately.

This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use.

The upstream `LICENSE`, `LICENSE.md`, copyright notices, warranty disclaimers, and indemnification
language are intentionally retained. This fork is public, open source, personal, and
noncommercial. Commercial, for-profit, or closed-source use of sunnypilot-authored material
requires prior written permission from its author(s).

The customizations do not intentionally modify driver monitoring, excessive-actuation checks,
`opendbc/safety/`, panda safety firmware, or AGNOS. Automated checks reject changes to those
protected areas. Passing those checks is not a warranty and does not make an update safe to use;
every release still requires manual review and in-vehicle validation.

comma.ai support may require reproducing any hardware problem on the latest stock openpilot
release. See the upstream projects for their current terms, safety rules, and support policies:

- https://github.com/commaai/openpilot
- https://github.com/sunnypilot/sunnypilot
- https://comma.ai/terms
- https://comma.ai/support

AI tools assisted with portions of the customization and maintenance automation. The maintainer
remains responsible for reviewing, testing, and understanding every published change.
