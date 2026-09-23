#!/usr/bin/env node
/**
 * The /marketing app's pure client logic (TASK-2026-01487): the router, the calendar arithmetic
 * and the composer's rules. Plain node, no runner and no npm install, the shape of the repo's
 * other JS guards.
 *
 * The assertions worth the most:
 *
 *   - **Every route survives a round trip.** `parseRoute(buildRoute(view, arg))` is the same view
 *     and argument, so a link the app builds is a link the app can open after a hard refresh.
 *   - **Calendar days never drift.** The arithmetic runs on `YYYY-MM-DD` strings and UTC dates,
 *     so it gives the same answer in every time zone and across daylight-saving changes; this
 *     script is run under several `TZ` values to prove it.
 *   - **A drag moves the day, never the hour.** `moveToDay` keeps the time of day.
 *   - **The preview mirrors the publishers.** Instagram never gets the link, Facebook and
 *     LinkedIn put it in the text only beside media, YouTube always appends it.
 *   - **The payload carries only what `save_post` takes.** No status, approver or owner.
 *
 * Loads the modules directly: they are plain ES modules with no DOM and no fetch. If one grows a
 * browser import this fails loudly rather than asserting nothing.
 */

const path = require('path');
const { pathToFileURL } = require('url');
const { execFileSync } = require('child_process');

const DIR = path.join(__dirname, '..', 'erpnext_enhancements', 'public', 'js', 'marketing');

let failures = 0;
function check(label, actual, expected) {
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${a}, want ${e}`);
	}
}

async function load(name) {
	try {
		return await import(pathToFileURL(path.join(DIR, name)).href);
	} catch (err) {
		console.error(`COULD NOT LOAD ${name}: it must stay free of DOM and fetch imports.`);
		console.error(err && err.message);
		process.exit(2);
	}
}

(async () => {
	const R = await load('routes.js');
	const C = await load('calendar.js');
	const K = await load('composer.js');

	console.log(`marketing client (TZ=${process.env.TZ || 'default'})\n`);

	// ------------------------------------------------------------------ routes
	const trips = [
		[R.VIEW_MONTH, ''],
		[R.VIEW_MONTH, '2026-10'],
		[R.VIEW_WEEK, '2026-09-21'],
		[R.VIEW_NEW, ''],
		[R.VIEW_NEW, '2026-09-23'],
		[R.VIEW_POST, 'SPOST-00001'],
		[R.VIEW_RESULTS, 'SPOST-00001'],
		[R.VIEW_QUEUE, ''],
		[R.VIEW_MEDIA, ''],
	];
	for (const [view, arg] of trips) {
		const parsed = R.parseRoute(R.buildRoute(view, arg));
		const got = [parsed.view, parsed.month || parsed.day || parsed.name];
		check(`round trip ${view} ${arg || '(none)'}`, got, [view, arg]);
	}
	check('bare /marketing is this month', R.parseRoute('/marketing').view, R.VIEW_MONTH);
	check('unknown section lands on the calendar', R.parseRoute('/marketing/nonsense/x').view, R.VIEW_MONTH);
	check('a bad month is ignored, not trusted', R.parseRoute('/marketing/calendar/2026-13').month, '');
	check('an impossible day is ignored', R.parseRoute('/marketing/week/2026-02-30').day, '');
	check('a query string is not part of the route', R.parseRoute('/marketing/queue?x=1').view, R.VIEW_QUEUE);
	check('a post name is decoded', R.parseRoute('/marketing/post/SPOST%2000001').name, 'SPOST 00001');
	check('a week link without a day is the current week', R.buildRoute(R.VIEW_WEEK, 'junk'), '/marketing/week');
	check('the week and month views share the calendar tab', [R.tabOf(R.VIEW_WEEK), R.tabOf(R.VIEW_MONTH)], ['calendar', 'calendar']);
	check('a post belongs to no tab', R.tabOf(R.VIEW_POST), '');

	// ------------------------------------------------------------------ calendar
	check('addDays across a month end', C.addDays('2026-09-30', 1), '2026-10-01');
	check('addDays across a year end', C.addDays('2026-12-31', 1), '2027-01-01');
	check('addDays across the March DST change', C.addDays('2026-03-07', 2), '2026-03-09');
	check('addDays across the November DST change', C.addDays('2026-10-31', 2), '2026-11-02');
	check('addDays back across a leap day', C.addDays('2028-03-01', -1), '2028-02-29');
	check('weekdayOf a known Tuesday', C.weekdayOf('2026-09-22'), 2);
	check('weekStartIndex', [C.weekStartIndex('Sunday'), C.weekStartIndex('Monday'), C.weekStartIndex('nonsense')], [0, 1, 0]);
	check('startOfWeek, Sunday start', C.startOfWeek('2026-09-22', 0), '2026-09-20');
	check('startOfWeek, Monday start', C.startOfWeek('2026-09-22', 1), '2026-09-21');
	check('startOfWeek on the start day itself', C.startOfWeek('2026-09-20', 0), '2026-09-20');
	check('addMonths across a year', [C.addMonths('2026-12', 1), C.addMonths('2026-01', -1)], ['2027-01', '2025-12']);
	check('monthLabel', C.monthLabel('2026-09'), 'September 2026');

	const sept = C.monthGrid('2026-09', 0);
	check('September 2026 (Sunday start) is five weeks', sept.length, 5);
	check('its grid starts on the Sunday before the 1st', sept[0][0], '2026-08-30');
	check('and ends on the Saturday after the 30th', sept[sept.length - 1][6], '2026-10-03');
	check('every week has seven days', sept.every((w) => w.length === 7), true);
	check('the grid has no gaps', sept.flat().every((d, i, all) => i === 0 || C.addDays(all[i - 1], 1) === d), true);
	const feb = C.monthGrid('2026-02', 0);
	check('February 2026 starts on a Sunday: four whole weeks', feb.length, 4);
	check('May 2026 with a Monday start is five weeks', C.monthGrid('2026-05', 1).length, 5);
	const aug = C.monthGrid('2026-08', 0);
	check('August 2026 (1st on a Saturday) needs six weeks', aug.length, 6);
	check('and six weeks still fit the server window (45 days)', C.windowOf(aug.flat()), { start: '2026-07-26', end: '2026-09-05' });

	check('weekDays, Monday start', C.weekDays('2026-09-23', 1), [
		'2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25', '2026-09-26', '2026-09-27',
	]);
	check('windowOf', C.windowOf(['2026-09-20', '2026-09-21', '2026-09-26']), { start: '2026-09-20', end: '2026-09-26' });

	check('datePart / timePart', [C.datePart('2026-09-23 09:05:00'), C.timePart('2026-09-23 09:05:00')], ['2026-09-23', '09:05']);
	check('timePart of a T-separated value', C.timePart('2026-09-23T17:30'), '17:30');
	check('datePart of nothing', C.datePart(null), '');
	check('a drag keeps the time of day', C.moveToDay('2026-09-23 17:45:00', '2026-09-30'), '2026-09-30 17:45:00');
	check('an unscheduled draft dropped gets 9 AM', C.moveToDay('', '2026-09-30'), '2026-09-30 09:00:00');

	const buckets = C.bucketByDay([
		{ name: 'b', scheduled_at: '2026-09-23 15:00:00' },
		{ name: 'a', scheduled_at: '2026-09-23 09:00:00' },
		{ name: 'c', scheduled_at: '2026-09-24 09:00:00' },
		{ name: 'x', scheduled_at: null },
	]);
	check('bucketByDay sorts within a day and drops the unscheduled', [Object.keys(buckets).sort(), buckets['2026-09-23'].map((p) => p.name)], [['2026-09-23', '2026-09-24'], ['a', 'b']]);

	check('clockLabel', [C.clockLabel('00:05'), C.clockLabel('09:00'), C.clockLabel('12:30'), C.clockLabel('23:59')], ['12:05 AM', '9:00 AM', '12:30 PM', '11:59 PM']);
	check('whenLabel', C.whenLabel('2026-09-23 09:05:00'), 'Wed, Sep 23, 9:05 AM');
	check('dayLabel', C.dayLabel('2026-09-20'), 'Sun, Sep 20');
	check('isPast compares days, not times', [C.isPast('2026-09-21', '2026-09-22'), C.isPast('2026-09-22', '2026-09-22')], [true, false]);

	// ------------------------------------------------------------------ composer
	check('characters counts an emoji once', K.characters('hi 🌊'), 4);
	check('utf8Bytes counts an emoji as four', K.utf8Bytes('hi 🌊'), 7);
	check('hashtags', K.hashtags('#fountains and #water_feature but not a#b'), 2);
	check('mentions', K.mentions('@sapphire and @other.co, not me@x'), 2);

	const limits = { Instagram: { text: 2200, hashtags: 30, mentions: 20 }, LinkedIn: { text: 3000 }, YouTube: { text_bytes: 5000 } };
	const ig = K.counters('Instagram', 'x'.repeat(2201), limits);
	check('Instagram counts characters, hashtags and mentions', ig.map((c) => c.label), ['characters', 'hashtags', '@mentions']);
	check('over the caption limit is over', K.isOver(ig[0]), true);
	check('YouTube counts bytes', K.counters('YouTube', '🌊', limits)[0], { label: 'bytes of description', value: 4, max: 5000 });
	check('Facebook has no counted limit', K.counters('Facebook', 'abc', limits)[0].max, null);

	check('scheduledAt', [K.scheduledAt('2026-09-23', '14:05'), K.scheduledAt('2026-09-23', ''), K.scheduledAt('', '14:05')], ['2026-09-23 14:05:00', '2026-09-23 09:00:00', '']);

	const state = {
		title: ' Launch ',
		body: 'The plaza fountain',
		link: 'https://sapphirefountains.com/plaza',
		link_title: 'Plaza',
		link_description: 'A new fountain',
		day: '2026-09-30',
		time: '10:00',
		video_title: 'Plaza build',
		video_tags: 'fountains, water',
		thumbnail: { name: 'MMA-9' },
		youtube_playlist_id: '',
		targets: [
			{ social_account: 'SACC-Facebook-1', network: 'Facebook', label: 'Sapphire', variant_text: '', first_comment: 'Hi' },
			{ social_account: 'SACC-Instagram-2', network: 'Instagram', label: '@sapphire', variant_text: 'IG words', first_comment: '' },
			{ social_account: 'SACC-LinkedIn-3', network: 'LinkedIn', label: 'Sapphire Co', variant_text: '', first_comment: '' },
			{ social_account: 'SACC-YouTube-4', network: 'YouTube', label: 'Channel', variant_text: '', first_comment: 'ignored' },
		],
		media: [],
		status: 'Approved',
		approver: 'someone@example.com',
		owner: 'x@example.com',
	};
	const payload = K.payloadOf(state);
	check('the payload carries exactly the fields save_post takes', Object.keys(payload).sort(), [
		'body', 'link', 'link_description', 'link_title', 'media', 'scheduled_at', 'targets', 'title', 'video_tags', 'video_thumbnail', 'video_title', 'youtube_playlist_id',
	]);
	check('and no workflow field rides along', ['status', 'approver', 'approved_at', 'owner'].some((k) => k in payload), false);
	check('the time is joined site-local', payload.scheduled_at, '2026-09-30 10:00:00');
	check('the thumbnail travels by name', payload.video_thumbnail, 'MMA-9');
	check('targets carry only their three fields', Object.keys(payload.targets[0]).sort(), ['first_comment', 'social_account', 'variant_text']);
	check('isDirty sees a change', K.isDirty(state, { ...state, body: 'other' }), true);
	check('isDirty ignores what the server does not take', K.isDirty(state, { ...state, status: 'Draft' }), false);
	check('networksOf, in chosen order, once each', K.networksOf({ targets: [...state.targets, state.targets[0]] }), ['Facebook', 'Instagram', 'LinkedIn', 'YouTube']);
	check('moved', [K.moved(['a', 'b', 'c'], 2, -1), K.moved(['a', 'b'], 0, -1)], [['a', 'c', 'b'], ['a', 'b']]);

	const [fb, igt, li, yt] = state.targets;
	const fbText = K.previewOf(state, fb);
	check('Facebook without media: text alone, link as a card', [fbText.text, fbText.card && fbText.card.url], ['The plaza fountain', state.link]);
	const photo = { name: 'MMA-1', asset_type: 'Image' };
	const video = { name: 'MMA-2', asset_type: 'Video' };
	const withMedia = { ...state, media: [photo, video] };
	check('Facebook with media: the link joins the text', K.previewOf(withMedia, fb).text, `The plaza fountain\n\n${state.link}`);
	const igPreview = K.previewOf(state, igt);
	check('Instagram uses its variant and never the link', [igPreview.text, igPreview.card], ['IG words', null]);
	check('and says the link is not sent', igPreview.notes.some((n) => n.includes('not sent')), true);
	const liCard = K.previewOf(state, li);
	check('LinkedIn without media: an article card with our title', liCard.card, { url: state.link, title: 'Plaza', description: 'A new fountain' });
	check('LinkedIn with media: the link joins the text', K.previewOf(withMedia, li).text.endsWith(state.link), true);
	const ytPreview = K.previewOf(withMedia, yt);
	check('YouTube: title, description with the link, the one video', [ytPreview.title, ytPreview.text.endsWith(state.link), ytPreview.media.map((m) => m.name)], ['Plaza build', true, ['MMA-2']]);
	check('YouTube has no first comment', ytPreview.first_comment, '');
	check('a link already in the text is not added twice', K.previewOf({ ...withMedia, body: `See ${state.link}` }, fb).text, `See ${state.link}`);

	// ------------------------------------------------------------------ tracking tags (TASK-2026-01488)
	// The same vectors tests/test_marketing_metrics.py runs against publish/tracking.py: the
	// preview must show exactly the link each publisher sends.
	const vectors = JSON.parse(
		require('fs').readFileSync(path.join(__dirname, '..', 'erpnext_enhancements', 'marketing', 'publish', 'tracking_vectors.json'), 'utf8')
	);
	if (vectors.tagged.length < 10) {
		console.error('MARKERS NOT FOUND: tracking_vectors.json has too few cases.');
		process.exit(2);
	}
	for (const c of vectors.tagged) check(`tagged: ${c.why}`, K.tagged(c.link, c.network, c.post, c.campaign), c.out);
	for (const c of vectors.with_link) check(`withSentLink: ${c.why}`, K.withSentLink(c.text, c.link, c.sent), c.out);

	const saved = { ...state, name: 'SPOST-00012', campaign: 'Spring Launch', link: 'https://www.sapphirefountains.com/plaza' };
	const tag = (net) => `https://www.sapphirefountains.com/plaza?utm_source=${net}&utm_medium=social&utm_campaign=spring-launch&utm_content=spost-00012`;
	check('a saved post previews the Facebook card tagged', K.previewOf(saved, fb).card.url, tag('facebook'));
	check('LinkedIn beside media: the tagged link rides in the text', K.previewOf({ ...saved, media: [photo] }, li).text.endsWith(tag('linkedin')), true);
	check('YouTube: the description ends with the tagged link', K.previewOf({ ...saved, media: [video] }, yt).text.endsWith(tag('youtube')), true);
	check('Instagram still gets no link, tagged or not', K.previewOf(saved, igt).text, 'IG words');
	check('the author\'s own copy in the text is tagged in place', K.previewOf({ ...saved, body: `See ${saved.link} now` }, fb).text, `See ${tag('facebook')} now`);
	const unsaved = K.previewOf({ ...saved, name: '' }, fb);
	check('an unsaved draft shows the plain link and says tags come on save', [unsaved.card.url, unsaved.notes.some((n) => n.includes('once the post is saved'))], [saved.link, true]);
	check('somebody else\'s link is never tagged or noted', K.previewOf({ ...saved, link: 'https://example.com/x' }, fb).notes.some((n) => n.includes('Tracking')), false);

	console.log('');
	if (failures) {
		console.error(`${failures} assertion(s) failed`);
		process.exit(1);
	}

	// Run the calendar again in zones either side of the site's: day arithmetic that leaned on
	// the local clock would drift in one of them. The child runs only the assertions above.
	if (!process.env.EE_TZ_CHILD) {
		for (const tz of ['Pacific/Honolulu', 'Asia/Kolkata', 'Pacific/Kiritimati']) {
			try {
				execFileSync(process.execPath, [__filename], { env: { ...process.env, TZ: tz, EE_TZ_CHILD: '1' }, stdio: 'pipe' });
				console.log(`  ok   the same answers under TZ=${tz}`);
			} catch (err) {
				console.error(`  FAIL under TZ=${tz}:\n${err.stderr || err.stdout}`);
				process.exit(1);
			}
		}
	}
	console.log('marketing client: all assertions passed');
})();
