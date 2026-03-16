import { fixName, generateTrackName, pad } from "./pathtemplates.js";

test("fix name", async () => {
	const fixed = fixName("track/:*");
	expect(fixed).toBe("track___");
});

function createMockTrack(overrides: Record<string, any> = {}) {
	return {
		id: 492212992,
		originalID: 0,
		title: "Therapy (Club Mix)",
		mainArtist: { id: 10620, name: "Armin van Buuren" },
		artists: ["Armin van Buuren"],
		artistsString: "Armin van Buuren",
		fullArtistsString: "Armin van Buuren",
		mainArtistsString: "Armin van Buuren",
		featArtistsString: "",
		album: {
			title: "Test Album",
			mainArtist: { id: 10620, name: "Armin van Buuren" },
			trackTotal: 1,
			discTotal: 1,
			genre: [],
			label: "Test",
			barcode: "",
			id: "62976362",
		},
		trackNumber: 1,
		discNumber: 1,
		date: { year: "2018" },
		dateString: "2018-05-25",
		bpm: 132,
		ISRC: "NLF711804279",
		explicit: false,
		playlist: null,
		position: null,
		...overrides,
	};
}

const mockSettings = {
	illegalCharacterReplacer: "_",
	paddingSize: 0,
	padTracks: true,
	padSingleDigit: true,
};

test("track_id uses originalID when available", () => {
	const track = createMockTrack({ originalID: 496430132 });
	const result = generateTrackName(
		"%track_id%",
		track as any,
		mockSettings as any
	);
	expect(result).toBe("496430132");
});

test("track_id falls back to id when originalID is 0", () => {
	const track = createMockTrack({ originalID: 0 });
	const result = generateTrackName(
		"%track_id%",
		track as any,
		mockSettings as any
	);
	expect(result).toBe("492212992");
});

test("pad name", () => {
	const settings = {
		paddingSize: 0,
		padTracks: true,
		padSingleDigit: true,
	};

	expect(pad(1, 12, settings)).toEqual("01");
	expect(pad(12, 12, settings)).toEqual("12");

	settings.paddingSize = 4;
	expect(pad(1, 2, settings)).toEqual("0001");
	expect(pad(12, 12, settings)).toEqual("0012");

	settings.padSingleDigit = false;
	settings.paddingSize = 1;
	expect(pad(1, 12, settings)).toEqual("1");
	expect(pad(12, 12, settings)).toEqual("12");

	settings.padTracks = false;

	expect(pad(1, 12, settings)).toEqual("1");
	expect(pad(12, 12, settings)).toEqual("12");
});
