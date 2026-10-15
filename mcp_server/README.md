# MPFB MCP server

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants (Claude Code, Claude Desktop, any MCP
client) build characters with MPFB: create humans, shape them with macros and modifiers, equip hair and clothes,
rig and pose them, render previews to look at, write new clothes with MakeClothes, and export to glTF.

It talks to MPFB's remote control server (`RemoteControlService`, a newline-delimited JSON protocol on
`127.0.0.1:9877`) and exposes the `AgentService` commands as typed tools. No arbitrary code runs in Blender.

## Running

Inside Blender: *MPFB > System > Remote control (MCP) > Start remote control*, or enable *Start remote control on
startup* in the MPFB preferences. Online access must be allowed (*Preferences > System > Network*).

Then register the server with your client, for example in a project's `.mcp.json` for Claude Code:

```json
{
  "mcpServers": {
    "mpfb": {
      "command": "uv",
      "args": ["run", "--quiet", "--directory", "/path/to/mpfb2/mcp_server", "mpfb-mcp"],
      "env": {"MPFB_BLENDER": "/Applications/Blender.app/Contents/MacOS/blender"}
    }
  }
}
```

| Variable | Default | Meaning |
|---|---|---|
| `MPFB_HOST`, `MPFB_PORT` | `127.0.0.1`, `9877` | Where MPFB listens |
| `MPFB_BLENDER` | unset | If set and nothing listens, start this Blender headless (`-b`) serving MPFB; it exits with the MCP server |
| `MPFB_BLENDER_ARGS` | unset | Extra Blender arguments for the headless instance |
| `MPFB_TIMEOUT` | `600` | Seconds to wait for one command |

## Tools

Humans: `info`, `reset_scene`, `list_humans`, `describe_human`, `create_human`, `set_macros`, `list_modifiers`,
`set_modifiers`. Assets: `list_assets`, `show_asset_thumbnails`, `equip_asset`, `unequip_asset`, `set_skin`,
`set_material`. Clothes: `get_joints`, `derive_clothes`, `cut_clothes`, `roll_edges`, `inflate`, `solidify`,
`join_objects`, `make_tube`, `make_belt`, `make_glasses`, `paint_strokes`, `list_vertex_groups`, `extract_clothes`,
`write_clothes`, `create_delete_group`, `set_visibility`, `delete_object`. Rig: `add_rig`, `list_poses`,
`set_pose`, `pose_bones`, `keyframe_pose`. Output: `render_views`, `save_blend`, `open_blend`, `export_gltf`.
See [docs/remote-control.md](../docs/remote-control.md).
