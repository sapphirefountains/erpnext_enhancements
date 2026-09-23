# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Will each network accept this post? Checked before approval, never at publish time.

TASK-2026-01483: a post a network will refuse must be caught while someone can still fix it, not
at 9am Tuesday when it fails in public view of nobody. So these checks run when a Social Post is
saved (the form shows them), and ``outbox.enqueue_problems`` refuses to queue a post that has any.
The figures are Meta's own, checked 2026-09-22 (``publish/constants.py``). LinkedIn (01484) and
YouTube (01485) add their networks here.

**Pure.** ``media`` is a list of dicts carrying the Marketing Media Asset fields that matter:
``asset_type``, ``mime_type``, ``width``, ``height``, ``duration_seconds``, and the source fields
``media.url_problem`` reads. An unknown dimension or type is a problem for Instagram, which
refuses what it cannot handle, and not for Facebook, which is permissive.
"""

import re

from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import media as M

HASHTAG = re.compile(r"(?<![\w&])#\w+")
MENTION = re.compile(r"(?<![\w.])@[\w.]+")


def _name(item):
	return item.get("asset") or item.get("name") or item.get("title") or "An asset"


def _is_video(item):
	return (item.get("asset_type") or "") == "Video"


def _ratio(item):
	try:
		width, height = float(item.get("width") or 0), float(item.get("height") or 0)
	except (TypeError, ValueError):
		return None
	return width / height if width > 0 and height > 0 else None


def _duration(item):
	try:
		value = float(item.get("duration_seconds") or 0)
	except (TypeError, ValueError):
		return None
	return value if value > 0 else None


def _reachable(media):
	return [p for p in (M.url_problem(item) for item in media) if p]


def _is_document(item):
	return (item.get("asset_type") or "") == "Document"


def _no_documents(network, media):
	return [
		f"{network}: {_name(item)} is a document; only LinkedIn posts documents"
		for item in media
		if _is_document(item)
	]


def facebook_problems(text, link, media, first_comment="", link_title="", post=None):
	problems = _no_documents("Facebook", media)
	media = [m for m in media if not _is_document(m)]
	if not (text or "").strip() and not (link or "").strip() and not media:
		problems.append("Facebook: a post needs text, a link or media")
	videos = [m for m in media if _is_video(m)]
	if videos and (len(videos) > 1 or len(videos) != len(media)):
		problems.append("Facebook: one video per post, and not mixed with photos")
	for item in media:
		mime = (item.get("mime_type") or "").lower()
		if not _is_video(item) and mime and mime not in P.FACEBOOK_IMAGE_TYPES:
			problems.append(f"Facebook: {_name(item)} is {mime}; photos must be JPEG, PNG, GIF, BMP or TIFF")
	return problems + [f"Facebook: {p}" for p in _reachable(media)]


def instagram_problems(text, link, media, first_comment="", link_title="", post=None):
	problems = _no_documents("Instagram", media)
	media = [m for m in media if not _is_document(m)]
	caption = (text or "").strip()
	if not media:
		problems.append("Instagram: a post needs at least one photo or video")
	if len(media) > P.INSTAGRAM_CAROUSEL_MAX:
		problems.append(f"Instagram: at most {P.INSTAGRAM_CAROUSEL_MAX} photos and videos in one post")
	if len(caption) > P.INSTAGRAM_CAPTION_MAX:
		problems.append(
			f"Instagram: the caption is {len(caption)} characters; the limit is {P.INSTAGRAM_CAPTION_MAX}"
		)
	hashtags = len(HASHTAG.findall(caption))
	if hashtags > P.INSTAGRAM_HASHTAGS_MAX:
		problems.append(f"Instagram: {hashtags} hashtags; the limit is {P.INSTAGRAM_HASHTAGS_MAX}")
	mentions = len(MENTION.findall(caption))
	if mentions > P.INSTAGRAM_MENTIONS_MAX:
		problems.append(f"Instagram: {mentions} @mentions; the limit is {P.INSTAGRAM_MENTIONS_MAX}")
	for item in media:
		name = _name(item)
		mime = (item.get("mime_type") or "").lower()
		ratio = _ratio(item)
		if _is_video(item):
			if mime not in P.INSTAGRAM_VIDEO_TYPES:
				problems.append(
					f"Instagram: {name} must be an MP4 or MOV video (type is {mime or 'not set'})"
				)
			duration = _duration(item)
			if duration is None:
				problems.append(
					f"Instagram: {name} needs its duration set, to check Instagram's 3 s to 15 min"
				)
			elif not P.INSTAGRAM_REEL_SECONDS_MIN <= duration <= P.INSTAGRAM_REEL_SECONDS_MAX:
				problems.append(f"Instagram: {name} runs {duration:g} s; videos must be 3 s to 15 min")
			if ratio is None:
				problems.append(f"Instagram: {name} needs its width and height set")
			elif not P.INSTAGRAM_REEL_RATIO_MIN <= ratio <= P.INSTAGRAM_REEL_RATIO_MAX:
				problems.append(f"Instagram: {name} has an aspect ratio Instagram refuses ({ratio:.2f}:1)")
		else:
			if mime not in P.INSTAGRAM_IMAGE_TYPES:
				problems.append(f"Instagram: {name} must be a JPEG (type is {mime or 'not set'})")
			if ratio is None:
				problems.append(
					f"Instagram: {name} needs its width and height set, to check the 4:5 to 1.91:1 ratio"
				)
			elif not P.INSTAGRAM_IMAGE_RATIO_MIN - 1e-6 <= ratio <= P.INSTAGRAM_IMAGE_RATIO_MAX + 1e-6:
				problems.append(
					f"Instagram: {name} is {ratio:.2f}:1; photos must be between 4:5 (0.80) and 1.91:1"
				)
	return problems + [f"Instagram: {p}" for p in _reachable(media)]


def linkedin_problems(text, link, media, first_comment="", link_title="", post=None):
	"""LinkedIn (TASK-2026-01484). We upload the bytes ourselves, so private files are fine."""
	problems = []
	commentary = (text or "").strip()
	link = (link or "").strip()
	if not commentary and not link and not media:
		problems.append("LinkedIn: a post needs text, a link or media")
	if len(commentary) > P.LINKEDIN_COMMENTARY_MAX:
		problems.append(
			f"LinkedIn: the text is {len(commentary)} characters; the limit is {P.LINKEDIN_COMMENTARY_MAX}"
		)
	videos = [m for m in media if _is_video(m)]
	documents = [m for m in media if _is_document(m)]
	images = [m for m in media if not _is_video(m) and not _is_document(m)]
	if videos:
		problems.append(
			"LinkedIn: video posts are not supported yet; post it without the video, or to YouTube"
		)
	if documents and (len(documents) > 1 or images or videos):
		problems.append("LinkedIn: a document goes on its own -- one per post, with no photos or videos")
	for item in documents:
		mime = (item.get("mime_type") or "").lower()
		if mime not in P.LINKEDIN_DOCUMENT_TYPES:
			problems.append(
				f"LinkedIn: {_name(item)} must be a PDF, PowerPoint or Word file (type is {mime or 'not set'})"
			)
	if len(images) > P.LINKEDIN_IMAGES_MAX:
		problems.append(f"LinkedIn: at most {P.LINKEDIN_IMAGES_MAX} photos in one post")
	for item in images:
		mime = (item.get("mime_type") or "").lower()
		if mime not in P.LINKEDIN_IMAGE_TYPES:
			problems.append(
				f"LinkedIn: {_name(item)} must be a JPG, PNG or GIF (type is {mime or 'not set'})"
			)
		try:
			pixels = float(item.get("width") or 0) * float(item.get("height") or 0)
		except (TypeError, ValueError):
			pixels = 0
		if pixels >= P.LINKEDIN_IMAGE_PIXELS_MAX:
			problems.append(
				f"LinkedIn: {_name(item)} is over LinkedIn's {P.LINKEDIN_IMAGE_PIXELS_MAX:,} pixels"
			)
	if link and not media:
		title = (link_title or "").strip()
		if not title:
			problems.append(
				"LinkedIn: a link post needs a Link Title -- LinkedIn does not read the page to make its preview"
			)
		elif len(title) >= P.LINKEDIN_ARTICLE_TITLE_MAX:
			problems.append(
				f"LinkedIn: the Link Title must be under {P.LINKEDIN_ARTICLE_TITLE_MAX} characters"
			)
	problems.extend(f"LinkedIn: {p}" for p in (M.bytes_problem(item) for item in media) if p)
	return problems


def tags_length(tags):
	"""YouTube's count of a tag list: commas between tags count, and a tag with a space is counted
	as if quoted (two more). Pure."""
	tags = [t for t in tags if t]
	return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


def split_tags(value):
	return [t.strip() for t in (value or "").split(",") if t.strip()]


def youtube_problems(text, link, media, first_comment="", link_title="", post=None):
	"""YouTube (TASK-2026-01485). The post text is the description; Video Title is the title.

	``post`` carries ``video_title``, ``video_tags``, ``youtube_playlist_id`` and, when a thumbnail
	is chosen, ``video_thumbnail_asset`` (that asset's fields).
	"""
	post = post or {}
	problems = []
	videos = [m for m in media if _is_video(m)]
	if len(videos) != 1 or len(media) != 1:
		problems.append(
			"YouTube: a post needs exactly one video and nothing else (choose a Thumbnail separately)"
		)
	for item in videos:
		mime = (item.get("mime_type") or "").lower()
		if mime and not (mime.startswith("video/") or mime == "application/octet-stream"):
			problems.append(f"YouTube: {_name(item)} is {mime}, not a video")
		problem = M.bytes_problem(item)
		if problem:
			problems.append(f"YouTube: {problem}")
	title = (post.get("video_title") or "").strip()
	if not title:
		problems.append("YouTube: the video needs a Video Title (the post's Title is only for ERPNext)")
	elif len(title) > P.YOUTUBE_TITLE_MAX:
		problems.append(
			f"YouTube: the Video Title is {len(title)} characters; the limit is {P.YOUTUBE_TITLE_MAX}"
		)
	description = (text or "").strip()
	if len(description.encode("utf-8")) > P.YOUTUBE_DESCRIPTION_MAX_BYTES:
		problems.append(f"YouTube: the description is over {P.YOUTUBE_DESCRIPTION_MAX_BYTES} bytes")
	for label, value in (("Video Title", title), ("description", description)):
		if "<" in value or ">" in value:
			problems.append(f"YouTube: the {label} may not contain < or >")
	tags = split_tags(post.get("video_tags"))
	if tags_length(tags) > P.YOUTUBE_TAGS_MAX:
		problems.append(
			f"YouTube: the tags come to {tags_length(tags)} characters; the limit is {P.YOUTUBE_TAGS_MAX}"
		)
	thumbnail = post.get("video_thumbnail_asset")
	if thumbnail:
		mime = (thumbnail.get("mime_type") or "").lower()
		if (thumbnail.get("asset_type") or "") != "Image" or mime not in P.YOUTUBE_THUMBNAIL_TYPES:
			problems.append("YouTube: the Thumbnail must be a JPEG or PNG image")
		problem = M.bytes_problem(thumbnail)
		if problem:
			problems.append(f"YouTube: {problem}")
	playlist = (post.get("youtube_playlist_id") or "").strip()
	if playlist and not all(c.isalnum() or c in "_-" for c in playlist):
		problems.append("YouTube: the Playlist ID is the list=... value from the playlist's address")
	if (first_comment or "").strip():
		problems.append(
			"YouTube: a first comment is not supported yet; leave it empty for the YouTube account"
		)
	return problems


#: network -> checker.
CHECKS = {
	P.NETWORK_FACEBOOK: facebook_problems,
	P.NETWORK_INSTAGRAM: instagram_problems,
	P.NETWORK_LINKEDIN: linkedin_problems,
	P.NETWORK_YOUTUBE: youtube_problems,
}


def problems(network, text, link, media, first_comment="", link_title="", post=None):
	"""Why ``network`` would refuse this post, as sentences; empty if it would accept it. Pure."""
	check = CHECKS.get(network)
	return check(text, link, list(media), first_comment, link_title, post) if check else []


def post_problems(post, targets, media, networks):
	"""Every network's problems for a Social Post. ``networks`` maps account name to network. Pure.

	Each target is checked with its own text (``variant_text``, else the post's ``body``).
	"""
	found = []
	for target in targets:
		network = networks.get(target.get("social_account"))
		text = (target.get("variant_text") or "").strip() or (post.get("body") or "")
		for problem in problems(
			network,
			text,
			post.get("link"),
			media,
			target.get("first_comment") or "",
			post.get("link_title") or "",
			post,
		):
			if problem not in found:
				found.append(problem)
	return found
