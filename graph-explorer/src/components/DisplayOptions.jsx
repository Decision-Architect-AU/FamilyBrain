import React, { useState } from 'react'
import { useStore } from '../store'

export default function DisplayOptions() {
  const { display, setDisplay } = useStore()
  const [open, setOpen] = useState(false)

  return (
    <div className="relative shrink-0">
      <button
        onClick={() => setOpen(!open)}
        className="px-2 py-1.5 text-xs bg-surface border border-border rounded text-gray-300 hover:border-accent/50"
      >Display ▾</button>

      {open && (
        <div className="absolute top-full mt-1 right-0 z-50 bg-surface border border-border rounded shadow-xl w-56 p-3 space-y-3">
          <div>
            <p className="text-xs text-gray-500 mb-1 font-medium">Node label</p>
            {[['name','Name'], ['id','Node ID'], ['label','Label only'], ['label+name','Label + Name']].map(([v, l]) => (
              <label key={v} className="flex items-center gap-2 text-xs text-gray-300 py-0.5 cursor-pointer">
                <input type="radio" checked={display.nodeLabel === v}
                  onChange={() => setDisplay({ nodeLabel: v })} className="accent-accent" />
                {l}
              </label>
            ))}
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1 font-medium">Edge label</p>
            {[['type','Rel type'], ['none','Hide'], ['id','Edge ID']].map(([v, l]) => (
              <label key={v} className="flex items-center gap-2 text-xs text-gray-300 py-0.5 cursor-pointer">
                <input type="radio" checked={display.edgeLabel === v}
                  onChange={() => setDisplay({ edgeLabel: v })} className="accent-accent" />
                {l}
              </label>
            ))}
          </div>
          <div className="space-y-1">
            {[
              ['showOrphans',   'Show orphan nodes'],
              ['showArrows',    'Show arrows'],
              ['scaleByDegree', 'Scale by degree'],
              ['showPropBadge', 'Property count badge'],
            ].map(([k, l]) => (
              <label key={k} className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                <input type="checkbox" checked={display[k]}
                  onChange={(e) => setDisplay({ [k]: e.target.checked })} className="accent-accent" />
                {l}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
