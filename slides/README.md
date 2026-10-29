# London Office Monitoring presentation

[Open the five-slide PDF](london-office-monitoring.pdf) · [Editable PowerPoint](london-office-monitoring.pptx) · [Content and presenter notes](CONTENT.md) · [Page previews](previews/)

The presentation explains the engineering deliverable and how the repository implements an evidence agent:

1. The standalone Python agent and its evidence scope.
2. The agentic loop, five fixed tools, checked observations and conversation state.
3. Pivotal modules and repository paths, with each module's responsibility.
4. Curated inputs, read-only access, provenance and deterministic comparisons, illustrated by one market example.
5. Installation, verification and the recorded live evaluation's scope.

The PowerPoint keeps the architecture diagram, repository table and text editable. Speaker notes preserve source pointers and qualifications, also available in [plain text](CONTENT.md). The PDF and previews show the published five-slide version. Presentation software is optional for running the Python agent.

From the repository root, check the published artifacts:

```sh
python3 scripts/verify_slides.py
```

This checks the manifest hashes, five PDF pages, five PNG previews, the editable five-slide PPTX with presenter notes and the content document. It does not judge answer quality or presentation usefulness. See [Architecture](../docs/user/ARCHITECTURE.md), [Agentic Q&A](../docs/user/AGENTIC_QA.md) and [Reproduce the examples](../docs/user/REPRODUCING.md) for the working implementation and trial commands.
