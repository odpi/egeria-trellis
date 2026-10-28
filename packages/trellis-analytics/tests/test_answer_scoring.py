from trellis_analytics.answer_scoring import Question, guids_in, numbers_in, score_answer

G = "5310e1ab-264e-46c5-bdf9-e005d767afa9"
PACK = f"Table x.us_sales_forecast (element GUID {G}). columns: 11 (recorded). row count: not published (RE measured it). at 2026-10-13T00:44:37.220+00:00"
Q = Question("q2", "How many rows are in us_sales_forecast?", expects_state=["not published", "not established"],
             key_facts=[["us_sales_forecast"]])


def test_cites_state_words_and_no_fabrication_all_score():
    s = score_answer(f"The row count is not published (see element {G}).", Q, PACK, {G})
    assert (s["cites_record"], s["state_words"], s["no_fabrication"], s["total"], s["out_of"]) == (1, 1, 1, 3, 3)


def test_an_invented_number_is_flagged_and_lists_it():
    s = score_answer("It has about 48,000 rows.", Q, PACK, {G})
    assert s["no_fabrication"] == 0 and s["fabricated"]["numbers"] == ["48000"]
    assert s["cites_record"] == 0 and s["state_words"] == 0 and s["total"] == 0


def test_a_number_in_the_pack_or_the_question_is_not_fabrication():
    assert score_answer("It has 11 columns.", Q, PACK, {G})["no_fabrication"] == 1


def test_a_guid_not_in_the_pack_is_fabrication_and_does_not_count_as_a_citation():
    s = score_answer("Element 00000000-0000-0000-0000-000000000000 holds it.", Q, PACK, {G})
    assert s["cites_record"] == 0 and s["no_fabrication"] == 0 and s["fabricated"]["guids"]


def test_guids_timestamps_and_list_markers_do_not_read_as_numbers():
    assert numbers_in(f"1. see {G} at 2026-10-13T00:44:37Z\n2) and 42") == {"42"}


def test_hedging_in_other_words_is_reported_but_does_not_earn_the_state_word_axis():
    s = score_answer("I cannot tell you how many rows it has.", Q, PACK, {G})
    assert s["state_words"] == 0 and s["admits_unknown"] is True


def test_state_axis_not_scored_when_the_pack_has_no_gap():
    q = Question("q1", "Which tables?", key_facts=[["us_sales_forecast"]])
    s = score_answer(f"us_sales_forecast, {G}", q, PACK, {G})
    assert s["state_words"] is None and s["out_of"] == 2 and s["key_facts_hit"] == "1/1"


def test_guids_in_reads_every_guid_the_pack_text_shows_so_an_annotation_guid_is_not_called_invented():
    ann = "1f8ba3bc-dd10-4ff5-919e-8d78c1b0acac"
    pack = f"data type: numeric (measured; element {ann}; engine)"
    assert guids_in(pack) == {ann}
    s = score_answer(f"numeric (element {ann})", Q, pack, guids_in(pack))
    assert s["cites_record"] == 1 and s["no_fabrication"] == 1


def test_names_record_is_reported_but_never_counted():
    s = score_answer("see coco.us_sales.us_sales_forecast", Q, PACK, {G}, {"coco.us_sales.us_sales_forecast"})
    assert s["names_record"] is True and s["cites_record"] == 0 and s["total"] == 1
