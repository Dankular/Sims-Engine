"""
ursina_app/entities/room_builder.py — Builds the isometric multiplayer room.

Visual style: warm Habbo Hotel / Neopets palette.
  - Checkered floor (cream + soft teal tiles)
  - Colored border walls
  - Scattered furniture (table, chairs, plant, bookcase, rug)
  - Ambient decorations

All entities parented to a root Entity so the whole room can be
shown/hidden as one unit.
"""
from __future__ import annotations

from ursina import Entity, Vec3, color, destroy

ROOM_SIZE  = 10   # 10×10 grid
TILE_SIZE  = 1.0
WALL_H     = 2.5

# Tile colors — alternating checkerboard
TILE_A = color.rgb(240, 220, 190)   # warm cream
TILE_B = color.rgb(180, 210, 200)   # soft teal

# Wall color
WALL_COLOR     = color.rgb(100, 140, 180)
WALL_TRIM      = color.rgb(70,  100, 140)
FLOOR_BORDER   = color.rgb(160, 130, 100)

# Furniture colors
FURNITURE = {
    "table":     color.rgb(180, 130,  80),
    "chair":     color.rgb(220, 100,  80),
    "plant":     color.rgb( 80, 180,  80),
    "rug":       color.rgb(180,  80, 150),
    "bookcase":  color.rgb(120,  90,  60),
    "lamp":      color.rgb(240, 200,  80),
}


def build_room(parent: Entity) -> list[Entity]:
    """
    Construct the full room and return a list of all created entities.
    All entities are parented to `parent` so destroying parent clears the room.
    """
    entities = []

    def make(model, col, pos, scale=(1, 1, 1), rot=(0, 0, 0)) -> Entity:
        e = Entity(
            model=model, color=col, position=pos, scale=scale,
            rotation=rot, parent=parent,
        )
        entities.append(e)
        return e

    # ── Floor tiles ────────────────────────────────────────────────────────────
    for x in range(ROOM_SIZE):
        for z in range(ROOM_SIZE):
            tile_col = TILE_A if (x + z) % 2 == 0 else TILE_B
            make("cube", tile_col, Vec3(x, -0.05, z), scale=(1, 0.08, 1))

    # Floor border
    for x in range(-1, ROOM_SIZE + 1):
        for z in (-1, ROOM_SIZE):
            make("cube", FLOOR_BORDER, Vec3(x, -0.05, z), scale=(1, 0.08, 1))
    for z in range(ROOM_SIZE):
        for x in (-1, ROOM_SIZE):
            make("cube", FLOOR_BORDER, Vec3(x, -0.05, z), scale=(1, 0.08, 1))

    # ── Walls ──────────────────────────────────────────────────────────────────
    # Back wall (z = ROOM_SIZE)
    make("cube", WALL_COLOR,
         Vec3(ROOM_SIZE / 2 - 0.5, WALL_H / 2, ROOM_SIZE + 0.5),
         scale=(ROOM_SIZE + 2, WALL_H, 0.3))
    # Left wall (x = -1)
    make("cube", WALL_COLOR,
         Vec3(-1.15, WALL_H / 2, ROOM_SIZE / 2 - 0.5),
         scale=(0.3, WALL_H, ROOM_SIZE + 2))
    # Wall trim strips
    make("cube", WALL_TRIM,
         Vec3(ROOM_SIZE / 2 - 0.5, 0.15, ROOM_SIZE + 0.5),
         scale=(ROOM_SIZE + 2, 0.3, 0.31))
    make("cube", WALL_TRIM,
         Vec3(-1.15, 0.15, ROOM_SIZE / 2 - 0.5),
         scale=(0.31, 0.3, ROOM_SIZE + 2))

    # ── Furniture ──────────────────────────────────────────────────────────────

    # Round table near center
    make("cylinder", FURNITURE["table"], Vec3(4, 0.45, 5), scale=(0.8, 0.1, 0.8))
    make("cylinder", FURNITURE["table"], Vec3(4, 0.22, 5), scale=(0.15, 0.45, 0.15))

    # Chairs around table
    for dx, dz in [(-1.2, 0), (1.2, 0), (0, -1.2), (0, 1.2)]:
        make("cube", FURNITURE["chair"],
             Vec3(4 + dx, 0.3, 5 + dz), scale=(0.55, 0.08, 0.55))
        make("cube", FURNITURE["chair"],
             Vec3(4 + dx, 0.55, 5 + dz + (0.22 if dz >= 0 else -0.22)),
             scale=(0.55, 0.5, 0.08))

    # Large rug
    make("quad", FURNITURE["rug"],
         Vec3(4, 0.01, 5), scale=(3.5, 3.5), rot=(90, 0, 0))

    # Bookcase against back wall
    make("cube", FURNITURE["bookcase"],
         Vec3(1, 0.75, 8.7), scale=(1.5, 1.5, 0.4))
    for i in range(3):
        shelf_col = color.rgb(180 - i * 30, 100, 80)
        make("cube", shelf_col,
             Vec3(1, 0.15 + i * 0.45, 8.55), scale=(1.3, 0.08, 0.1))

    # Plant in left corner
    make("cylinder", FURNITURE["plant"],
         Vec3(0.5, 0.5, 8.5), scale=(0.4, 1.0, 0.4))
    make("sphere", color.rgb(50, 170, 50),
         Vec3(0.5, 1.15, 8.5), scale=(0.7, 0.7, 0.7))
    make("cylinder", color.rgb(140, 100, 60),
         Vec3(0.5, 0.2, 8.5), scale=(0.5, 0.4, 0.5))

    # Lamp near right side
    make("cylinder", FURNITURE["lamp"],
         Vec3(7.5, 0.8, 7.5), scale=(0.15, 1.6, 0.15))
    make("sphere", color.rgb(255, 245, 180),
         Vec3(7.5, 1.7, 7.5), scale=(0.45, 0.45, 0.45))

    # Small side table
    make("cube", FURNITURE["table"],
         Vec3(8, 0.35, 1), scale=(0.8, 0.08, 0.8))
    make("cube", FURNITURE["table"],
         Vec3(8, 0.17, 1), scale=(0.1, 0.35, 0.1))

    # Decorative wall art
    make("quad", color.rgb(200, 170, 220),
         Vec3(3, 1.5, 9.85), scale=(1.5, 1.0), rot=(0, 0, 0))
    make("quad", color.rgb(170, 220, 200),
         Vec3(6, 1.5, 9.85), scale=(1.2, 0.9), rot=(0, 0, 0))

    return entities


def destroy_room(entities: list[Entity]) -> None:
    for e in entities:
        destroy(e)
