import { describe, test, expect, vi } from "vitest";
import { GW } from "../gw.js";

describe("getTrackRedirectInfo", () => {
	test("detects redirect when resolved SNG_ID differs from requested ID", async () => {
		const gw = new GW(null, {});

		const mockGwTrack = {
			SNG_ID: 492212992,
			SNG_TITLE: "Therapy (Club Mix)",
			DURATION: 168,
			MD5_ORIGIN: 0,
			MEDIA_VERSION: 0,
			FILESIZE: 0,
			ALB_TITLE: "",
			ALB_PICTURE: "",
			ART_ID: 10620,
			ART_NAME: "Armin van Buuren",
			ISRC: "NLF711804279",
			FALLBACK: { SNG_ID: 492212993 },
			ALBUM_FALLBACK: {
				data: [{ ALB_ID: "100001" }, { ALB_ID: "100002" }],
			},
		};

		vi.spyOn(gw, "get_track_with_fallback").mockResolvedValue(
			mockGwTrack as any
		);

		const info = await gw.getTrackRedirectInfo(496430132);
		expect(info.requestedID).toBe(496430132);
		expect(info.resolvedID).toBe(492212992);
		expect(info.isRedirected).toBe(true);
		expect(info.fallbackID).toBe(492212993);
		expect(info.alternativeAlbumIDs).toEqual(["100001", "100002"]);
	});

	test("reports no redirect when IDs match", async () => {
		const gw = new GW(null, {});

		const mockGwTrack = {
			SNG_ID: 492212992,
			SNG_TITLE: "Test Track",
			DURATION: 200,
			MD5_ORIGIN: 0,
			MEDIA_VERSION: 0,
			FILESIZE: 0,
			ALB_TITLE: "",
			ALB_PICTURE: "",
			ART_ID: 1,
			ART_NAME: "Test",
			ISRC: "TEST",
		};

		vi.spyOn(gw, "get_track_with_fallback").mockResolvedValue(
			mockGwTrack as any
		);

		const info = await gw.getTrackRedirectInfo(492212992);
		expect(info.requestedID).toBe(492212992);
		expect(info.resolvedID).toBe(492212992);
		expect(info.isRedirected).toBe(false);
		expect(info.fallbackID).toBe(0);
		expect(info.alternativeAlbumIDs).toEqual([]);
	});

	test("handles string track IDs", async () => {
		const gw = new GW(null, {});

		const mockGwTrack = {
			SNG_ID: 492212992,
			SNG_TITLE: "Test Track",
			DURATION: 200,
			MD5_ORIGIN: 0,
			MEDIA_VERSION: 0,
			FILESIZE: 0,
			ALB_TITLE: "",
			ALB_PICTURE: "",
			ART_ID: 1,
			ART_NAME: "Test",
			ISRC: "TEST",
		};

		vi.spyOn(gw, "get_track_with_fallback").mockResolvedValue(
			mockGwTrack as any
		);

		const info = await gw.getTrackRedirectInfo("496430132");
		expect(info.requestedID).toBe(496430132);
		expect(info.resolvedID).toBe(492212992);
		expect(info.isRedirected).toBe(true);
	});
});
