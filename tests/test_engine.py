import tempfile
import unittest
from pathlib import Path
import pymupdf as f
from engine import edit, extract, atomic_save, pages_from_text, content_rect, visible_rect


class Operations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name)/'input.pdf'
        self.out = Path(self.tmp.name)/'output.pdf'
        with f.open() as d:
            for n in range(4):
                p = d.new_page(width=400 if n%2 == 0 else 600, height=500)
                p.insert_text((40,60),'SECRET CONTENT',fontsize=14)
                p.insert_text((40,300),f'KEEP PAGE {n+1}',fontsize=14)
                p.set_rotation(n*90)
            d.set_metadata({'author':'PRIVATE METADATA'})
            d.embfile_add('private.txt',b'PRIVATE ATTACHMENT')
            d.save(self.source)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ranges(self):
        self.assertEqual(pages_from_text('1-3,2,4',4),[0,1,2,3])
        self.assertEqual(pages_from_text('3-1,1',4,False),[2,1,0,0])
        for s in ('0','5','1,,2','1-2-3',''):
            with self.assertRaises(ValueError):
                pages_from_text(s,4)

    def test_crop_rotation_and_offset(self):
        box = (.1,.15,.85,.9)
        edit(self.source,self.out,'crop',list(range(4)),box)
        with f.open(self.source) as a, f.open(self.out) as b:
            for i in range(4):
                self.assertAlmostEqual(b[i].rect.width,a[i].rect.width*.75,places=3)
                self.assertAlmostEqual(b[i].rect.height,a[i].rect.height*.75,places=3)
                # Cropped rendered content must match the selected source region.
                pa=a[i].get_pixmap(clip=visible_rect(a[i],box))
                pb=b[i].get_pixmap()
                self.assertEqual((pa.width,pa.height),(pb.width,pb.height))
                self.assertEqual(pa.samples,pb.samples)
        second = Path(self.tmp.name)/'second.pdf'
        edit(self.out,second,'crop',list(range(4)),(.1,.1,.9,.9))
        with f.open(self.out) as a, f.open(second) as b:
            for i in range(4):
                self.assertAlmostEqual(b[i].rect.width,a[i].rect.width*.8,places=3)
        edit(second,self.out,'reset_crop',list(range(4)))
        with f.open(self.source) as a, f.open(self.out) as b:
            self.assertEqual(a[1].rect,b[1].rect)

    def test_true_text_redaction(self):
        # Scope to unrotated first page; other pages deliberately retain their text.
        edit(self.source,self.out,'redact',[0],(.05,.05,.8,.2))
        with f.open(self.out) as d:
            self.assertNotIn('SECRET',d[0].get_text())
            self.assertIn('KEEP PAGE 1',d[0].get_text())
            self.assertIn('SECRET',d[1].get_text())
            self.assertEqual(d.embfile_count(),0)
            self.assertFalse(d.metadata.get('author'))

    def test_image_redaction(self):
        src = Path(self.tmp.name)/'image.pdf'
        pixels = f.Pixmap(f.csRGB, f.IRect(0,0,100,100), False)
        pixels.clear_with(80)
        with f.open() as d:
            p=d.new_page(width=100,height=100)
            p.insert_image(p.rect,pixmap=pixels)
            d.save(src)
        edit(src,self.out,'redact',[0],(.2,.2,.8,.8))
        with f.open(self.out) as d:
            image=d[0].get_pixmap()
            self.assertEqual(image.pixel(50,50),(255,255,255))
            self.assertEqual(image.pixel(5,5),(80,80,80))
            for item in d[0].get_images():
                raw=f.Pixmap(d,item[0])
                self.assertEqual(raw.pixel(50,50),(255,255,255))

    def test_text_rotations(self):
        edit(self.source,self.out,'text',list(range(4)),(.1,.5,.9,.8),text='Added {page} / {pages}',size=12)
        with f.open(self.out) as d:
            for i,p in enumerate(d):
                self.assertIn(f'Added {i+1} / 4',p.get_text())
                r = p.search_for(f'Added {i+1} / 4')[0] * p.rotation_matrix
                self.assertTrue(visible_rect(p,(.1,.5,.9,.8)).contains(r))

    def test_failed_edit_leaves_source_unchanged(self):
        original=self.source.read_bytes()
        with self.assertRaises(ValueError):
            edit(self.source,self.out,'text',[0],(.1,.1,.11,.11),text='Cannot fit',size=50)
        self.assertEqual(self.source.read_bytes(),original)
        self.assertFalse(self.out.exists())

    def test_merge_split_reorder_delete_encrypt(self):
        edit(self.source,self.out,'merge',[],files=[(str(self.source),'')])
        with f.open(self.out) as d:
            self.assertEqual(len(d),8)
        subset=Path(self.tmp.name)/'subset.pdf'
        extract(self.out,subset,[7,0,0])
        with f.open(subset) as d:
            self.assertEqual(len(d),3)
            self.assertIn('KEEP PAGE 4',d[0].get_text())
        edit(subset,self.out,'delete',[0])
        with f.open(self.out) as d:
            self.assertEqual(len(d),2)
            atomic_save(d,Path(self.tmp.name)/'locked.pdf','test-password')
        with f.open(Path(self.tmp.name)/'locked.pdf') as d:
            self.assertTrue(d.needs_pass)
            self.assertTrue(d.authenticate('test-password'))
        edit(self.source,self.out,'reorder',[3,2,1,0])
        with f.open(self.out) as d:
            self.assertIn('KEEP PAGE 4',d[0].get_text())

    def test_no_page_limit(self):
        many=Path(self.tmp.name)/'many.pdf'
        with f.open() as d:
            for _ in range(301):
                d.new_page(width=200,height=300)
            d.save(many)
        edit(many,self.out,'crop',list(range(301)),(.1,.1,.9,.9))
        with f.open(self.out) as d:
            self.assertEqual(len(d),301)
            self.assertEqual(d[-1].rect,f.Rect(0,0,160,240))


if __name__ == '__main__':
    unittest.main()
