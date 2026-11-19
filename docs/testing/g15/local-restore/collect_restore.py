"""Check the cold restore evidence against the original G15 deployment and archive a summary."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main():
    args = argparse.ArgumentParser()
    args.add_argument("evidence", type=Path)
    args.add_argument("run")
    args.add_argument("output", type=Path)
    parsed = args.parse_args()
    run = parsed.run

    def load(name):
        return json.loads((parsed.evidence / (name + ".json")).read_bytes())

    previous = json.loads((Path(__file__).resolve().parents[1] /
                           "local-https/deployment-result.json").read_bytes())
    original = previous["containers"]["backend"]["persistentSqliteVolume"]
    image_ids = {role: v["id"] for role, v in previous["images"].items()}

    def service(label, volume):
        containers = {name: load(label + "-" + name) for name in ("edge", "backend", "provider")}
        for role, spec in containers.items():
            assert spec["image"] == image_ids["web" if role == "edge" else "backend"], label
            published = {k: v for k, v in spec["ports"].items() if v}
            assert published == ({"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18443"}]}
                                 if role == "edge" else {}), (label, role, published)
        assert containers["backend"]["volume"] == volume
        assert (parsed.evidence / (label + "-network.txt")).read_text().strip() == "true"
        assert load(label + "-health")["data"]["status"] == "ok"
        return {"health": "ok", "backendDataVolume": volume, "backendImage": containers["backend"]["image"],
                "webImage": containers["edge"]["image"], "loopbackOnly": True, "businessNetworkInternal": True}

    def case(letter, source_volume, deployment):
        backup = load(letter + "-backup")
        restored = load(letter + "-restore")
        assert backup["manifestSha256"] == restored["manifestSha256"]
        assert backup["backup"]["files"] == restored["files"]
        source = backup["source"]["reading"]
        assert source == restored["reading"]
        assert source["session"] == load("probe-" + letter)
        assert source["integrityCheck"] == "ok" and source["foreignKeyViolations"] == 0
        dest = run + "-" + letter + "-restored-data"
        return {"sourceVolume": source_volume, "backupVolume": run + "-" + letter + "-backup",
                "restoredVolume": dest, "originalFileSha256": backup["source"]["files"],
                "backupFileSha256": backup["backup"]["files"], "manifestSha256": restored["manifestSha256"],
                "restoredBeforeStartupFileSha256": restored["files"], "restoredReading": source,
                "runningService": service(deployment, dest),
                "runningServiceSessionMatches": True}

    a = case("a", original, "a-service")
    assert load("a-backup")["source"] == load("original-after")
    assert a["restoredReading"]["session"] == previous["afterBrowserReadback"]["database"]
    a.update(sourceRecheckedUnchanged=True, matchesPreviousDeployment=True)

    b = case("b", run + "-b-source-data", "b-restored-service")
    assert b["restoredReading"]["session"] == load("probe-b-source")
    b["sourceService"] = service("b-source-service", b["sourceVolume"])
    b["restoredSourceLookup"] = load("b-restored-origin")
    assert set(b["restoredSourceLookup"].values()) == {False}
    b["illustration"] = load("b-image")
    image = b["illustration"]["sha256"]
    assert b["backupFileSha256"][".zhiya.sqlite3.media_pins/blobs/" + image + ".png"] == image
    assert ".zhiya.sqlite3.media_pins.bindings/11.sha256" in b["backupFileSha256"]
    assert ".zhiya.sqlite3.media_pins/versions/11.json" in b["backupFileSha256"]
    assert "fixture-source/g15-restore-paper-boat.png" in b["originalFileSha256"]
    assert "fixture-source/g15-restore-paper-boat.png" not in b["backupFileSha256"]
    b["sourceSupplierCalls"] = [json.loads(line) for line in
                                (parsed.evidence / "probe-b-source-supplier.txt").read_text().splitlines() if line]
    b["restoredSupplierCalls"] = [json.loads(line) for line in
                                  (parsed.evidence / "probe-b-supplier.txt").read_text().splitlines() if line]
    result = {"atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "applicationCommit": previous["commit"],
              "origin": previous["origin"], "images": image_ids, "previousTestVolume": a, "independentFamily": b}
    with parsed.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
