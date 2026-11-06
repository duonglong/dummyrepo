from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestHistoryPhoneWidth(HttpCase):
    browser_size = '375,800'

    def test_bug_b_0101_history_table_fits_phone_screen(self):
        """Regression test for B-0101: at 375 px the history table fits its box.

        Requirements: FR-RCT-002, NFR-USE-001
        """
        partner = self.env['res.partner'].create({'name': 'Phone width partner', 'phone': '555-0100'})
        partner.write({'phone': '555-0199'})
        self.assertTrue(partner.change_entry_ids)
        code = """
            const wait = async (sel) => {
                for (let i = 0; i < 100; i++) {
                    const el = document.querySelector(sel);
                    if (el) { return el; }
                    await new Promise((r) => setTimeout(r, 200));
                }
                throw new Error('missing ' + sel + ' in the page');
            };
            (await wait('button[name="change_history"]')).click();
            const table = await wait('.o_field_widget[name="change_entry_ids"] table.o_list_table');
            await wait('.o_field_widget[name="change_entry_ids"] tbody tr.o_data_row');
            const box = table.closest('.o_list_renderer');
            if (table.scrollWidth > box.clientWidth + 1) {
                throw new Error('history table ' + table.scrollWidth + ' px wide in a ' + box.clientWidth + ' px box');
            }
            console.log('test successful');
        """
        self.browser_js(
            '/odoo/action-base.action_partner_form/%s' % partner.id, 'void (async () => {' + code + '})()',
            login='admin')
