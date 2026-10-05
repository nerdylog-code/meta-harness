# Plugin SDK (later phase)

The typed contract every plugin kind implements (`RuntimeAdapter`,
`ToolProvider`, `ContextEngine`, `MemoryProvider`, `RagProvider`,
`SandboxProvider`, `WorkspaceProvider`, `ChannelProvider`, `VoiceProvider`,
`WorkflowProvider`, `RendererProvider`, `MetricsExporter`, `SkillProvider`,
`ModelProvider`) plus the manifest schema and the trust ladder.

Not in the skeleton: the plugin kernel arrives after the runtime contract is
frozen and one adapter exists (PROJECT_BOOK §10/§17).
