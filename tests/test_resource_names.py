from cs2asset.resource_names import model_material_names, resource_name


def test_readable_names_are_portable_and_preserve_useful_words():
    assert resource_name("Dirty Football") == "dirty_football"
    assert resource_name("Forest/Rock: café") == "forest_rock_cafe"
    assert resource_name("CON") == "asset_con"
    assert resource_name("???") == "asset"


def test_model_material_names_use_the_asset_and_disambiguate_materials():
    assert model_material_names("Lifebuoy", ["Rubber"]) == ["lifebuoy"]
    assert model_material_names("Lifebuoy", ["Rubber", "Rope"]) == [
        "lifebuoy_rubber",
        "lifebuoy_rope",
    ]
    assert model_material_names("Rock", ["Stone", "STONE"]) == [
        "rock_stone_m000",
        "rock_stone_m001",
    ]


def test_material_names_stay_distinct_after_truncation_and_suffix_collisions():
    names = model_material_names("a" * 64, ["left", "right"])
    assert len(set(names)) == 2 and all(len(name) <= 64 for name in names)
    names = model_material_names("rock", ["stone", "STONE", "stone_m000"])
    assert len(set(names)) == 3
