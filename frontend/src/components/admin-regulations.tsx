import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowRight, CheckCircle2, Eye, FileText, LoaderCircle, Pencil, RefreshCw, ScrollText, Upload, X } from 'lucide-react';
import { apiRequest, type Regulation, type RegulationVersion } from '@/lib/api';

type Props = { children: (content: ReactNode, openUpload: () => void) => ReactNode };
type RegulationForm = {
  name: string; description: string; department_name: string; jurisdiction: string;
  issuing_authority: string; document_type: string; business_types: string; source_url: string;
};
const emptyForm: RegulationForm = { name: '', description: '', department_name: '', jurisdiction: '', issuing_authority: '', document_type: 'regulation', business_types: '', source_url: '' };

function slugify(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}
function dateLabel(value?: string | null) {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value)) : '—';
}
function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}

export function AdminRegulationsPage({ children }: Props) {
  const [regulations, setRegulations] = useState<Regulation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState<'upload' | 'view' | 'edit' | null>(null);
  const [selected, setSelected] = useState<Regulation | null>(null);
  const [versions, setVersions] = useState<RegulationVersion[]>([]);
  const [form, setForm] = useState<RegulationForm>(emptyForm);
  const [newRegulation, setNewRegulation] = useState(false);
  const [version, setVersion] = useState('');
  const [pdf, setPdf] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const loadRegulations = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await apiRequest<{ items: Regulation[] }>('/admin/regulations');
      setRegulations(result.items);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void loadRegulations(); }, [loadRegulations]);

  const openUpload = () => {
    setSelected(null); setVersions([]); setForm(emptyForm); setVersion(''); setPdf(null);
    setNewRegulation(regulations.length === 0); setDialog('upload');
  };
  const openDetails = async (regulation: Regulation) => {
    setSelected(regulation); setVersions([]); setDialog('view');
    try {
      const result = await apiRequest<{ items: RegulationVersion[] }>(`/admin/regulations/${regulation.id}/versions`);
      setVersions(result.items);
    } catch (reason) { setError(errorText(reason)); }
  };
  const openEdit = (regulation: Regulation) => {
    setSelected(regulation);
    setForm({ name: regulation.name, description: regulation.description || '', department_name: regulation.department_name || '', jurisdiction: regulation.jurisdiction || '', issuing_authority: regulation.issuing_authority || '', document_type: regulation.document_type || 'regulation', business_types: regulation.business_types.join(', '), source_url: regulation.source_url || '' });
    setDialog('edit');
  };

  const submitUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!pdf) { setError('Choose a PDF to upload.'); return; }
    if (!version.trim()) { setError('Enter a version identifier.'); return; }
    setBusy(true); setError('');
    try {
      let regulation = selected;
      if (newRegulation) {
        if (!form.name.trim()) throw new Error('Enter a regulation name.');
        regulation = await apiRequest<Regulation>('/admin/regulations', {
          method: 'POST',
          body: JSON.stringify({
            name: form.name.trim(), slug: slugify(form.name), description: form.description,
            department_name: form.department_name || null,
            jurisdiction: form.jurisdiction || null,
            issuing_authority: form.issuing_authority || null,
            document_type: form.document_type,
            business_types: form.business_types.split(',').map((item) => item.trim()).filter(Boolean),
            source_url: form.source_url || null,
          }),
        });
        setSelected(regulation);
        setNewRegulation(false);
        await loadRegulations();
      }
      if (!regulation) throw new Error('Choose a regulation.');
      const data = new FormData(); data.set('file', pdf); data.set('version', version.trim());
      await apiRequest<RegulationVersion>(`/admin/regulations/${regulation.id}/versions`, { method: 'POST', body: data });
      setDialog(null); setNotice('PDF uploaded and processed. Activate the ready version when it should become current.');
      await loadRegulations();
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  };

  const submitEdit = async (event: FormEvent) => {
    event.preventDefault(); if (!selected) return;
    setBusy(true); setError('');
    try {
      await apiRequest(`/admin/regulations/${selected.id}`, { method: 'PATCH', body: JSON.stringify({
        name: form.name.trim(), description: form.description,
        department_name: form.department_name || null, jurisdiction: form.jurisdiction || null,
        issuing_authority: form.issuing_authority || null, document_type: form.document_type,
        business_types: form.business_types.split(',').map((item) => item.trim()).filter(Boolean),
        source_url: form.source_url || null,
      }) });
      setDialog(null); setNotice('Regulation details updated.'); await loadRegulations();
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  };

  const activateVersion = async (item: RegulationVersion) => {
    if (!selected) return;
    setBusy(true); setError('');
    try {
      await apiRequest(`/admin/regulations/${selected.id}/versions/${item.id}/activate`, { method: 'POST' });
      const result = await apiRequest<{ items: RegulationVersion[] }>(`/admin/regulations/${selected.id}/versions`);
      setVersions(result.items); setSelected({ ...selected, active_version_id: item.id, active_version: item });
      await loadRegulations(); setNotice(`Version ${item.version} is now active.`);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  };

  const reprocessVersion = async (item: RegulationVersion) => {
    if (!selected) return;
    setBusy(true); setError('');
    try {
      await apiRequest(`/admin/regulations/${selected.id}/versions/${item.id}/reprocess`, { method: 'POST' });
      const result = await apiRequest<{ items: RegulationVersion[] }>(`/admin/regulations/${selected.id}/versions`);
      setVersions(result.items); setNotice(`Version ${item.version} was reprocessed.`);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  };

  const content = <>
    {error && <div className="callout regulation-alert" role="alert"><span>{error}</span><button className="icon-button" onClick={() => setError('')} aria-label="Dismiss error"><X size={14} /></button></div>}
    {notice && <div className="toast-note" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Dismiss notification"><X size={13} /></button></div>}
    {loading ? <div className="regulation-state"><LoaderCircle className="spin" size={22} /> Loading regulations…</div> : regulations.length === 0 ? <div className="surface pad regulation-state"><ScrollText size={25} /><h2>No regulations yet</h2><p className="muted-copy">Upload a regulation PDF to create the first regulatory record.</p><button className="button button--dark" onClick={openUpload}><Upload size={14} /> Upload a PDF</button></div> : <div className="admin-card-grid">{regulations.map((item) => <article className="surface pad regulation-card" key={item.id}>
      <div className="role-card__top"><span className="micro">{item.active_version_id ? 'Active regulation' : item.status === 'draft' ? 'Draft regulation' : 'Regulation'}</span><ScrollText size={18} /></div>
      <h2>{item.name}</h2><p className="muted-copy">{item.active_version ? `Version ${item.active_version.version}` : 'No active version'} · Updated {dateLabel(item.updated_at)}</p>
      <div className="row-actions"><button className="button button--light tiny-button" onClick={() => void openDetails(item)}><Eye size={12} /> View regulation</button><button className="button button--soft tiny-button" onClick={() => openEdit(item)}><Pencil size={12} /> Update</button></div>
    </article>)}</div>}
    {dialog && <div className="regulation-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) setDialog(null); }}><section className="surface pad regulation-dialog" role="dialog" aria-modal="true" aria-labelledby="regulation-dialog-title">
      <div className="section-head"><div><span className="micro">Admin / Knowledge</span><h2 id="regulation-dialog-title">{dialog === 'upload' ? 'Upload regulation PDF' : dialog === 'edit' ? 'Update regulation' : selected?.name}</h2></div><button className="icon-button" onClick={() => setDialog(null)} disabled={busy} aria-label="Close"><X size={16} /></button></div>
      {dialog === 'upload' && <form className="form-stack" onSubmit={submitUpload}>
        {regulations.length > 0 && <div className="field"><label>Regulation</label><select value={newRegulation ? '__new__' : selected?.id || ''} onChange={(event) => { if (event.target.value === '__new__') { setNewRegulation(true); setSelected(null); } else { setNewRegulation(false); setSelected(regulations.find((item) => item.id === event.target.value) || null); } }}><option value="">Choose a regulation</option><option value="__new__">Create a new regulation</option>{regulations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>}
        {newRegulation && <>
          <div className="field"><label>Regulation name</label><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div>
          <div className="field"><label>Description</label><textarea value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} rows={2} /></div>
          <div className="form-grid"><div className="field"><label>Department</label><input value={form.department_name} onChange={(event) => setForm({ ...form, department_name: event.target.value })} /></div><div className="field"><label>Jurisdiction</label><input value={form.jurisdiction} onChange={(event) => setForm({ ...form, jurisdiction: event.target.value })} /></div></div>
          <div className="form-grid"><div className="field"><label>Issuing authority</label><input value={form.issuing_authority} onChange={(event) => setForm({ ...form, issuing_authority: event.target.value })} /></div><div className="field"><label>Document type</label><input value={form.document_type} onChange={(event) => setForm({ ...form, document_type: event.target.value })} /></div></div>
          <div className="field"><label>Business types (comma separated)</label><input value={form.business_types} onChange={(event) => setForm({ ...form, business_types: event.target.value })} /></div>
          <div className="field"><label>Source URL</label><input type="url" value={form.source_url} onChange={(event) => setForm({ ...form, source_url: event.target.value })} /></div>
        </>}
        {!newRegulation && !selected && <p className="muted-copy">Select an existing regulation or choose “Create a new regulation”.</p>}
        <div className="form-grid"><div className="field"><label>Version</label><input required value={version} onChange={(event) => setVersion(event.target.value)} placeholder="e.g. 1.0" /></div><div className="field"><label>PDF document</label><input required type="file" accept="application/pdf,.pdf" onChange={(event) => setPdf(event.target.files?.[0] || null)} /></div></div>
        {pdf && <p className="muted-copy"><FileText size={13} /> {pdf.name} · {(pdf.size / 1024 / 1024).toFixed(2)} MB</p>}
        <div className="row-actions"><button type="button" className="button button--light" disabled={busy} onClick={() => setDialog(null)}>Cancel</button><button className="button button--dark" disabled={busy || (!newRegulation && !selected)}>{busy ? <><LoaderCircle className="spin" size={14} /> Processing PDF…</> : <><Upload size={14} /> Upload and process</>}</button></div>
      </form>}
      {dialog === 'edit' && <form className="form-stack" onSubmit={submitEdit}>
        <div className="field"><label>Regulation name</label><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div>
        <div className="field"><label>Description</label><textarea value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} rows={2} /></div>
        <div className="form-grid"><div className="field"><label>Department</label><input value={form.department_name} onChange={(event) => setForm({ ...form, department_name: event.target.value })} /></div><div className="field"><label>Jurisdiction</label><input value={form.jurisdiction} onChange={(event) => setForm({ ...form, jurisdiction: event.target.value })} /></div></div>
        <div className="form-grid"><div className="field"><label>Issuing authority</label><input value={form.issuing_authority} onChange={(event) => setForm({ ...form, issuing_authority: event.target.value })} /></div><div className="field"><label>Document type</label><input value={form.document_type} onChange={(event) => setForm({ ...form, document_type: event.target.value })} /></div></div>
        <div className="field"><label>Business types (comma separated)</label><input value={form.business_types} onChange={(event) => setForm({ ...form, business_types: event.target.value })} /></div><div className="field"><label>Source URL</label><input type="url" value={form.source_url} onChange={(event) => setForm({ ...form, source_url: event.target.value })} /></div>
        <div className="row-actions"><button type="button" className="button button--light" disabled={busy} onClick={() => setDialog(null)}>Cancel</button><button className="button button--dark" disabled={busy}>{busy ? 'Saving…' : 'Save changes'} <ArrowRight size={13} /></button></div>
      </form>}
      {dialog === 'view' && selected && <div className="regulation-details">
        <p className="muted-copy">{selected.description || 'No description provided.'}</p>
        <div className="form-grid"><div><span className="micro">Department</span><p>{selected.department_name || '—'}</p></div><div><span className="micro">Issuing authority</span><p>{selected.issuing_authority || '—'}</p></div><div><span className="micro">Jurisdiction</span><p>{selected.jurisdiction || '—'}</p></div><div><span className="micro">Document type</span><p>{selected.document_type}</p></div></div>
        <div className="metric-row"><span>Created</span><strong>{dateLabel(selected.created_at)}</strong></div>
        <h3>Versions</h3>{versions.length === 0 ? <p className="muted-copy">No versions uploaded.</p> : <div className="list-stack">{versions.map((item) => <div className="journey-card" key={item.id}><div><h3>{item.version} · {item.file_name}</h3><p>Uploaded {dateLabel(item.uploaded_at)} · Effective {dateLabel(item.effective_date)} · {item.page_count} pages · {item.chunk_count} chunks</p>{item.status === 'failed' && <p className="form-error">{item.processing_error || 'Processing failed'}</p>}</div><span className={`status ${item.status === 'ready' ? 'status--green' : item.status === 'failed' ? 'status--amber' : ''}`}>{item.id === selected.active_version_id ? 'active' : item.status}</span>{item.status === 'ready' && item.id !== selected.active_version_id && <button className="button button--soft tiny-button" disabled={busy} onClick={() => void activateVersion(item)}><CheckCircle2 size={12} /> Activate</button>}{item.status === 'failed' && <button className="button button--soft tiny-button" disabled={busy} onClick={() => void reprocessVersion(item)}><RefreshCw size={12} /> Reprocess</button>}</div>)}</div>}
        <div className="row-actions"><button className="button button--light" onClick={() => { setDialog('upload'); setNewRegulation(false); setVersion(''); setPdf(null); }}>Upload new version</button><button className="button button--light" onClick={loadRegulations}><RefreshCw size={13} /> Refresh</button></div>
      </div>}
    </section></div>}
  </>;

  return <>{children(content, openUpload)}</>;
}
